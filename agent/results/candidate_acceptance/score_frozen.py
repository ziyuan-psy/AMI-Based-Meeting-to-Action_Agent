"""Frozen scoring entry point. Gold is read here only; the Agent is never run.

Default: offline, fail on missing votes. --online explicitly permits judge requests.
--saved-dev replays the 16-meeting recorded candidate, without opening held-out data.
"""
from __future__ import annotations
import argparse,copy,hashlib,importlib.util,json,math,sys,time
from collections import Counter,defaultdict
from pathlib import Path
import campaign as c
from mta import config,quality,support
from mta.control import RunControl,RunStopped,launch
from mta.evaluate import TYPES,MATCH_SYSTEM,MATCH_SCHEMA,make_llm_matcher,prf,deadline_match
from mta.observation import SETTINGS,write_json,write_text,sha256
from mta.regression import RecordingLLM,score_items,is_placeholder
from mta.store import Store
from mta.trace import StageTrace
from mta.validate import validator

DEV=('ES2011','IS1008','TS3004','TS3006')
HELDOUT=('ES2004','TS3007')

class ScoringError(ValueError):pass
class MissingVotes(ScoringError):pass

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def text_key(ss,gg):return hashlib.sha256(json.dumps([[s['text'] for s in ss],[g['text'] for g in gg]]).encode()).hexdigest()[:20]
def gate(series,allow_heldout=False):
    if not series or len(set(series))!=len(series) or any(s not in DEV+HELDOUT for s in series):
        raise ScoringError('Select unique known series explicitly.')
    if not allow_heldout and any(s in HELDOUT for s in series):
        raise ScoringError('Held-out input/gold must not be opened without --allow-heldout.')

def frozen_protocol():
    study=c.load(c.ROOT/'study.json');p=c.load(c.HERE/'scoring_protocol.json')
    config.QUALITY_PROFILE=quality.RECALL_CANDIDATE
    if {name:getattr(config,name) for name in SETTINGS}!=study['runtime']['config']:
        raise ScoringError('Configuration/budgets differ from acceptance.')
    for rel,expected in study['source_sha256'].items():
        if sha256(c.REPO/rel)!=expected:raise ScoringError('Frozen runtime/schema changed: '+rel)
    if p['votes']!=3 or p['judge']!=study['runtime']['model'] or p['match_system']!=MATCH_SYSTEM or p['match_schema']!=MATCH_SCHEMA:
        raise ScoringError('Judge or alignment protocol changed.')
    spec=importlib.util.spec_from_file_location('_fixed_eval_helpers',config.ROOT/'scripts/full_eval.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    if set(p['fewshot_excluded_ids'])!=old.FEWSHOT_GOLD:raise ScoringError('Few-shot exclusion mismatch.')
    p={**p,'semantic_prompts':{name:getattr(old,name) for name in ('FAITH_SYSTEM','FAITH_SCHEMA','COVER_SYSTEM','COVER_SCHEMA')},
       'support_prompt':support.SYSTEM,'support_schema':support.SCHEMA,
       'scorer_source_sha256':{rel:study['source_sha256'][rel] for rel in
          ('agent/src/mta/evaluate.py','agent/src/mta/regression.py','agent/src/mta/support.py','agent/scripts/full_eval.py')},
       'adapter_sha256':sha256(Path(__file__))}
    return study,p,old

class OfflineJudge:
    def forced_tool(self,*args,**kwargs):raise MissingVotes('Required recorded judgment is missing; offline mode forbids API calls.')

class BudgetedJudge:
    def __init__(self,client):self.client=client
    def forced_tool(self,system,user,name,schema,**kw):
        kw.setdefault('stage','score_alignment' if name=='submit_matches' else 'score_support' if name=='submit_verdicts' else 'score_summary')
        result=self.client.forced_tool(system,user,name,schema,**kw)
        if name=='submit_matches' and (not isinstance(result,dict) or not isinstance(result.get('pairs'),list)):
            raise ScoringError('Malformed alignment response, retained in trace; no replacement vote requested.')
        return result

class VoteCache:
    """Preserve the original text key inside a model/protocol/type/full-input checked envelope."""
    def __init__(self,path,p,kind,ss,gg,control,legacy=None,import_root=None):
        self.path,self.control=path,control
        self.key=text_key(ss,gg)
        self.identity={'protocol_digest':digest(p),'judge':p['judge'],'type':kind,
                       'input_sha256':digest([[r['text'] for r in ss],[r['text'] for r in gg]]),
                       'system_texts':[r['text'] for r in ss],'gold_texts':[r['text'] for r in gg]}
        self.data={'identity':self.identity,'votes':{}}
        if path.exists():self.data=c.load(path)
        elif import_root is not None:
            source=import_root/path.parent.name/path.name
            if source.exists():
                self.data=c.load(source);self.data['imported_from']={'path':str(source),'sha256':sha256(source)}
        elif legacy is not None:
            saved=c.load(legacy)
            if self.key in saved:
                self.data.update(votes={self.key:copy.deepcopy(saved[self.key])},
                                 imported_from={'path':str(legacy),'sha256':sha256(legacy),'kind':'verified_saved_development_study'})
        if self.data['identity']!=self.identity:raise ScoringError('Vote cache model/protocol/type/input mismatch.')
        self.validate(self.data['votes'].get(self.key,[]))
        write_json(self.path,self.data,self.control)
    @staticmethod
    def validate(runs):
        if not isinstance(runs,list) or len(runs)>3 or any(not isinstance(r,list) for r in runs):
            raise ScoringError('Expected at most three saved alignment vote arrays.')
    def get(self,key,default=None):
        if key!=self.key:raise ScoringError('Unexpected alignment key.')
        return copy.deepcopy(self.data['votes'].get(key,default))
    def __setitem__(self,key,value):
        self.validate(value)
        old=self.data['votes'].get(key,[])
        if key!=self.key or len(value)<len(old) or value[:len(old)]!=old:
            raise ScoringError('Existing votes cannot be replaced or reordered.')
        self.data['votes'][key]=copy.deepcopy(value);write_json(self.path,self.data,self.control)

class SemanticCache:
    def __init__(self,path,p,control,import_root=None):
        self.path,self.control=path,control;self.accessed=[]
        self.identity={'protocol_digest':digest(p),'judge':p['judge']};self.data={'identity':self.identity,'entries':{}}
        if path.exists():self.data=c.load(path)
        elif import_root is not None and (import_root/path.parent.name/path.name).exists():
            source=import_root/path.parent.name/path.name;self.data=c.load(source)
            self.data['imported_from']={'path':str(source),'sha256':sha256(source)}
        if self.data['identity']!=self.identity:raise ScoringError('Semantic cache protocol mismatch.')
    def compute(self,key,make):
        if key not in self.data['entries']:
            self.data['entries'][key]=make();write_json(self.path,self.data,self.control)
        value=copy.deepcopy(self.data['entries'][key]);self.accessed.append(value)
        return value

def native_path(root,series,mid,mode,layout):
    if layout=='series':return root/series/mode/(mid+'.json')
    if layout=='native':return root/mode/(mid+'.json')
    candidates=[p for p in (root/series/mode/(mid+'.json'),root/mode/(mid+'.json')) if p.is_file()]
    if len(candidates)!=1:raise ScoringError('Missing or ambiguous prediction path: '+mid+'/'+mode)
    return candidates[0]

def validate_records(records,mid):
    errors=list(validator().iter_errors(records))
    if errors:raise ScoringError('Output schema error in '+mid+': '+errors[0].message)
    ids=[r['id'] for r in records]
    if len(set(ids))!=len(ids) or any(i.split('.')[0]!=mid for i in ids):raise ScoringError('Duplicate/wrong-meeting record ID.')
    visible={mid[:-1]+letter for letter in 'abcd'[:'abcd'.index(mid[-1])+1]}
    for row in records:
        for ref in row['reference']:
            if ref['source_type'] in ('dialogue','participant_metadata') and ref['locator'].split('.')[0] not in visible:
                raise ScoringError('Prediction cites a future/other-series meeting.')
        if any(r['id'].split('.')[0] not in visible-{mid} for r in row.get('related_items',[])):
            raise ScoringError('Prediction links outside same-series earlier meetings.')

def validate_gold(records,mid):
    errors=list(validator().iter_errors(records))
    if errors:raise ScoringError('Gold schema error in '+mid+': '+errors[0].message)
    ids=[r['id'] for r in records]
    if len(set(ids))!=len(ids) or any(i.split('.')[0]!=mid for i in ids):
        raise ScoringError('Duplicate/wrong-meeting gold ID.')

def verify_manifest(root):
    data=c.load(root/'manifest.json')['artifact_sha256']
    for rel,h in data.items():
        path=(root/rel).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha256(path)!=h:
            raise ScoringError('Artifact manifest mismatch: '+rel)
    return len(data)

def load_native(path,mid,mode,study):
    record=c.load(path);validate_records(record,mid)
    meta=c.load(path.with_suffix('.run.json'));res=meta['result']
    if meta['status']!='COMPLETED' or res['status']!='COMPLETED':raise ScoringError('Incomplete prediction is not an empty output: '+mid)
    if meta['meeting_id']!=mid or meta['mode']!=mode or res['output_sha256']!=sha256(path):raise ScoringError('Prediction identity/hash mismatch.')
    if meta['model']!=study['runtime']['model'] or meta['config']!=study['runtime']['config']:
        raise ScoringError('Prediction model/config differs from acceptance.')
    if meta['code']['source_sha256']!=study['runtime']['code']['source_sha256'] or meta['quality_policy']!=quality.effective(mode):
        raise ScoringError('Prediction code/effective policy differs from frozen candidate.')
    related_files=[(path.with_name(mid+'_report.md'),'report_sha256'),
                   (path.with_suffix('.p2.json'),'p2_sha256'),(path.with_suffix('.clarifications.json'),'clarifications_sha256')]
    for file,key in related_files:
        if not file.is_file() or sha256(file)!=res[key]:raise ScoringError('Missing/changed publication sidecar: '+str(file))
    return record,meta

def prepare_jobs(args,study):
    if args.saved_dev:
        if args.online or args.allow_heldout or args.semantics!='none':raise ScoringError('Saved development replay is offline alignment only.')
        root=args.saved_dev.resolve();saved=c.load(root/'study.json');gate(sorted({m[:-1] for m in saved['meetings']}))
        if any(m[:-1] not in DEV for m in saved['meetings']):raise ScoringError('Saved study contains non-development meetings.')
        verify_manifest(root)
        old_protocol=c.load(root/'scoring_protocol.json')
        jobs=[]
        for mid in saved['meetings']:
            folder=root/'replay'/mid
            jobs.append({'meeting_id':mid,'mode':'s2','prediction':folder/'record.json','gold':folder/'gold.json',
                         'legacy_votes':{t:folder/('issue_votes.json' if t=='problems' else 'stable_votes.json') for t in TYPES},
                         'expected_score':folder/'score.json','execution_kind':'FIXED_RESPONSE_REPLAY_NOT_FRESH_PIPELINE'})
        return jobs,{'cache_policy':'recorded_development_votes','saved_study_sha256':sha256(root/'study.json'),
                     'gold_version':'frozen development gold','legacy_protocol':old_protocol}
    gate(args.series,args.allow_heldout)  # before opening any native prediction/gold
    if args.run is None or args.gold is None:raise ScoringError('--run and --gold are required.')
    jobs=[];generation=None
    if (args.run/'campaign.json').is_file():
        generation=c.load(args.run/'campaign.json')
        if generation['status']!='COMPLETED':raise ScoringError('Generation campaign is incomplete; do not score a favorable subset.')
        if set(args.series)!=set(generation['series']) or set(args.modes)!=set(generation['modes']):
            raise ScoringError('Requested scoring population differs from frozen prediction campaign.')
    for series in args.series:
        for mode in args.modes:
            for letter in 'abcd':
                mid=series+letter;path=native_path(args.run,series,mid,mode,args.layout)
                jobs.append({'meeting_id':mid,'mode':mode,'prediction':path,'gold':args.gold/(mid+'.json'),
                             'execution_kind':'NATIVE_PREDICTION'})
    return jobs,{'cache_policy':generation['cache_policy'] if generation else 'native_metadata_see_per_meeting',
                 'generation_campaign_sha256':sha256(args.run/'campaign.json') if generation else None,
                 'gold_version':'hashes of explicitly selected gold files'}

def field_metrics(record,gold,score):
    ss={r['id']:r for r in record};gg={r['id']:r for r in gold};counts=defaultdict(lambda:[0,0])
    for sid,gid in score['matches'].items():
        s,g=ss[sid],gg[gid]
        checks={'status':s['status']==g['status'],'prior_context_needed':s['prior_context_needed']==g['prior_context_needed']}
        if s['type']=='actions':checks.update(owner_exact=set(s['owner'] or [])==set(g['owner'] or []),
            owner_basis=s['owner_basis']==g['owner_basis'],deadline_match=deadline_match(s,g),deadline_basis=s['deadline_basis']==g['deadline_basis'])
        for name,ok in checks.items():counts[name][0]+=bool(ok);counts[name][1]+=1
    return {k:{'correct':n,'total':d,'rate':round(n/d,3) if d else None} for k,(n,d) in counts.items()}

def semantics(judge,p,job,record,gold,folder,control,old,import_root):
    kind=job['semantics'];mid=job['meeting_id'];result={}
    transcript=config.TRANSCRIPTS/(mid+'.txt')
    meta=job['metadata']
    if sha256(transcript)!=meta['input']['sha256']:raise ScoringError('Semantic judge transcript differs from generation.')
    for context in (config.PARTICIPANTS,config.SCENARIO):
        if sha256(context)!=meta['context_sha256'][context.name]:raise ScoringError('Semantic judge context changed.')
    if kind in ('support','all'):
        items=[r for r in record if r['type']!='abstract'];store=Store(mid,True,job['prediction'].parent)
        cache=SemanticCache(folder/'support.json',p,control,import_root)
        verdicts=support.judge_meeting(judge,store,items,cache) if items else []
        if items and (len(cache.accessed[-1])!=len(items) or
                      {v.get('item') for v in cache.accessed[-1]}!=set(range(len(items)))):
            raise ScoringError('Duplicate/missing support verdict indexes.')
        for n,v in enumerate(verdicts):
            if (not v or v.get('item')!=n or v.get('statement') not in support.LEVELS
                or any(v.get(k) not in support.LEVELS+['na'] for k in ('owner','deadline'))):
                raise ScoringError('Missing/invalid support verdict; do not silently drop its denominator.')
        result['support']=[{'id':r['id'],'verdict':v} for r,v in zip(items,verdicts)]
    if kind in ('summary','all'):
        cache=SemanticCache(folder/'summary.json',p,control,import_root)
        sentences=[r['text'] for r in record if r['type']=='abstract'];refs=[r['text'] for r in gold if r['type']=='abstract' and not is_placeholder(r)]
        result['summary']={'faithfulness':old.faithfulness(judge,cache,mid,sentences),'coverage':old.coverage(judge,cache,sentences,refs)}
        for raw in cache.accessed:
            key,n,levels=('sentences',len(sentences),('supported','partial','unsupported')) if 'sentences' in raw else ('points',len(refs),('covered','partial','missed'))
            entries=raw.get(key,[])
            if (not isinstance(entries,list) or len(entries)!=n or
                any(not isinstance(v,dict) or type(v.get('index')) is not int or v.get('verdict') not in levels for v in entries) or
                {v['index'] for v in entries}!=set(range(n))):raise ScoringError('Duplicate/missing summary verdict indexes.')
        for metric in result['summary'].values():
            if metric['n'] and (type(metric.get('score')) is not int or not 1<=metric['score']<=5):raise ScoringError('Invalid summary score.')
            counts=('supported','partial','unsupported') if 'supported' in metric else ('covered','partial','missed')
            if metric['n'] and sum(metric.get(k,0) for k in counts)!=metric['n']:raise ScoringError('Incomplete summary verdicts.')
    return result

def score_job(job,p,out,online,old,votes_from=None):
    mid,mode=job['meeting_id'],job['mode'];key=mid+'.'+mode
    trace=StageTrace(out/'traces'/(key+'.jsonl'),meeting_id=mid,mode=mode)
    control=RunControl(trace=trace);start=time.monotonic()
    value={'meeting_id':mid,'mode':mode,'status':'RUNNING','execution_kind':job['execution_kind']}
    destination=out/'per_meeting'/(key+'.json');write_json(destination,value)
    def checkpoint(**fields):
        with control.guard():value.update(fields);write_json(destination,value,control)
    def work():
        judge=BudgetedJudge(RecordingLLM(trace).scoped(control)) if online else OfflineJudge()
        record=job['record'];gold=job['gold_items'];raw={};cache_proofs={}
        # All modes share exact-input judgments within a meeting, as in the original protocol.
        folder=out/'votes'/mid;previous=votes_from/'votes' if votes_from else None
        def matcher(ss,gg):
            if not ss and not gg:return {}
            kind=ss[0]['type'] if ss else gg[0]['type']
            if not ss or not gg:raw[kind]={};return {}
            cache=VoteCache(folder/(kind+'.'+text_key(ss,gg)+'.json'),p,kind,ss,gg,control,
                legacy=job.get('legacy_votes',{}).get(kind),import_root=previous)
            result=make_llm_matcher(judge,cache=cache,votes=3)(ss,gg)
            raw[kind]=result;cache_proofs[kind]={'path':str(cache.path.relative_to(out)),
                'votes':len(cache.data['votes'][cache.key]),'input_sha256':cache.identity['input_sha256']}
            return result
        score=score_items(record,gold,matcher,p['fewshot_excluded_ids'])
        if job.get('expected_score') and score!=c.load(job['expected_score']):raise ScoringError('Saved development score differs from original.')
        accounting={}
        for typ in TYPES:
            sys_rows=[r for r in record if r['type']==typ];gold_rows=[r for r in gold if r['type']==typ]
            excluded={r['id'] for r in gold_rows if r['id'] in p['fewshot_excluded_ids']}
            accounting[typ]={'raw_system':len(sys_rows),'raw_gold':len(gold_rows),'unscored':any(is_placeholder(r) for r in gold_rows),
                'excluded_gold_ids':sorted(excluded),'excluded_system_ids':sorted(s for s,g in raw.get(typ,{}).items() if g in excluded)}
        checkpoint(score=score,fields=field_metrics(record,gold,score),raw_alignment=raw,category_accounting=accounting,vote_caches=cache_proofs)
        if job['semantics']!='none':checkpoint(semantic=semantics(judge,p,job,record,gold,folder,control,old,previous))
    try:
        control.wait(launch(work),'scoring');value['status']='COMPLETED'
    except RunStopped as exc:value.update(status=exc.failure['status'],failure=exc.failure)
    except Exception as exc:value.update(status='FAILED',failure={'type':type(exc).__name__,
        'reason':str(exc) if isinstance(exc,ScoringError) else 'judge_or_artifact_error; transport details are limited to error type'})
    finally:
        control.seal();value=copy.deepcopy(value)
        value.update(runtime_control=control.snapshot(),judge_usage=control.usage_snapshot(),elapsed_seconds=round(time.monotonic()-start,3))
        trace.finish(stage='scoring',event='finished',status=value['status']);write_json(destination,value)
    return value

def aggregate(rows):
    result={}
    for typ in TYPES:
        eligible=[r['score']['metrics'][typ] for r in rows if 'unscored' not in r['score']['metrics'][typ]]
        result[typ]={**prf(*(sum(r[k] for r in eligible) for k in ('tp','sys','gold'))),'eligible_meetings':len(eligible)}
    result['pooled']=prf(*(sum(result[t][k] for t in TYPES) for k in ('tp','sys','gold')))
    return result

def relation_metrics(rows,jobs):
    amap={s:g for r in rows for s,g in r['score']['matches'].items()};tp=ns=ng=0
    for job in jobs:
        system={r['id']:r for r in job['record']};gold={r['id']:r for r in job['gold_items']}
        for sid,gid in amap.items():
            if sid not in system:continue
            sp={(r['id'],r['relation']) for r in system[sid].get('related_items',[])}
            gp={(r['id'],r['relation']) for r in gold[gid].get('related_items',[])}
            mapped={(amap[target],rel) for target,rel in sp if target in amap}
            tp+=len(mapped&gp);ns+=len(sp);ng+=len(gp)
    return {**prf(tp,ns,ng),'population':'matched non-abstract source items only; unmapped targets count as unmatched predictions',
            'all_predicted_links':sum(len(r.get('related_items',[])) for j in jobs for r in j['record'] if r['type']!='abstract')}

def summarize(rows,jobs,plan,out):
    modes={};complete=len(rows)==len(jobs) and all(r['status']=='COMPLETED' for r in rows)
    for mode in plan['modes']:
        selected=[j for j in jobs if j['mode']==mode];rs=[r for r in rows if r['mode']==mode and r['status']=='COMPLETED']
        ready=len(rs)==len(selected);fields={}
        for field in ('owner_exact','owner_basis','deadline_match','deadline_basis','status','prior_context_needed'):
            correct=sum(r['fields'].get(field,{}).get('correct',0) for r in rs);total=sum(r['fields'].get(field,{}).get('total',0) for r in rs)
            fields[field]={'correct':correct,'total':total,'rate':round(correct/total,3) if total else None}
        costs=[j['metadata'] for j in selected if j.get('metadata')]
        def distribution(values):
            if not values:return {'n':0,'mean':None,'p95':None}
            ordered=sorted(values)
            return {'n':len(values),'mean':round(sum(values)/len(values),3),'p95':ordered[math.ceil(.95*len(ordered))-1]}
        modes[mode]={'status':'COMPLETED' if ready else 'INCOMPLETE','expected_meetings':len(selected),'scored_meetings':len(rs),
            'metrics':aggregate(rs) if ready else None,'partial_metrics':None if ready else aggregate(rs),
            'matched_item_fields':fields,'relations':relation_metrics(rs,selected) if ready else None,
            'prediction_costs':{'meetings_with_native_costs':len(costs),'attempted_calls':sum(x['runtime_control']['attempted_calls'] for x in costs) if costs else None,
                'charged_tokens':sum(x['runtime_control']['charged_tokens'] for x in costs) if costs else None,
                'unknown_usage_tokens':sum(x['runtime_control']['unknown_usage_tokens'] for x in costs) if costs else None,
                'seconds':round(sum(x['elapsed_seconds'] for x in costs),3) if costs else None,'cache_policy':plan['generation']['cache_policy'],
                'latency_seconds':distribution([x['elapsed_seconds'] for x in costs]),
                'tokens':distribution([x['runtime_control']['charged_tokens'] for x in costs]),
                'calls':distribution([x['runtime_control']['attempted_calls'] for x in costs])},
            'judge_costs':{'attempted_calls':sum(r['runtime_control']['attempted_calls'] for r in rows if r['mode']==mode),
                'charged_tokens':sum(r['runtime_control']['charged_tokens'] for r in rows if r['mode']==mode)}}
        support_rows=[v for r in rs for v in r.get('semantic',{}).get('support',[])]
        if support_rows:
            counts=Counter(v['verdict']['statement'] for v in support_rows);n=sum(counts.values())
            modes[mode]['support']={**dict(counts),'n':n,'supported_rate':round(counts['supported']/n,3),
                                    'supported_or_partial_rate':round((counts['supported']+counts['partial'])/n,3)}
        summaries=[r['semantic']['summary'] for r in rs if 'summary' in r.get('semantic',{})]
        if summaries:
            modes[mode]['summary']={}
            for metric in ('faithfulness','coverage'):
                values=[x[metric]['score'] for x in summaries if x[metric]['score'] is not None]
                modes[mode]['summary'][metric]={'mean':round(sum(values)/len(values),3) if values else None,'meetings':len(values)}
    result={'status':'COMPLETED' if complete else 'INCOMPLETE','protocol_digest':plan['protocol_digest'],
            'scope':plan['scope'],'semantics':plan['semantics'],'modes':modes,
            'unscored_jobs':[j['meeting_id']+'.'+j['mode'] for j in jobs if not any(r['meeting_id']==j['meeting_id'] and r['mode']==j['mode'] and r['status']=='COMPLETED' for r in rows)],
            'note':'Model-judged matching; human review is separate. Failed/missing predictions are never treated as empty records.'}
    write_json(out/'summary.json',result)
    lines=['# Frozen evaluation scores','',f'Status: **{result["status"]}**. {plan["scope"]}',
        '',f'Semantic checks: {plan["semantics"]}. Three-vote alignment; raw vote caches retained.',
        '', '| Mode | Completed | Action F1 | Decision F1 | Issue F1 | Pooled P / R / F1 | Owner correct / matched |',
        '|---|---:|---:|---:|---:|---|---|']
    for mode,row in modes.items():
        a=row['metrics'];owner=row['matched_item_fields']['owner_exact']
        vals=[str(a[t]['f1']) for t in TYPES] if a else ['incomplete']*3
        pooled=' / '.join(str(a['pooled'][k]) for k in ('precision','recall','f1')) if a else 'not reported'
        lines.append(f'| {mode} | {row["scored_meetings"]}/{row["expected_meetings"]} | '+ ' | '.join(vals)+f' | {pooled} | {owner["correct"]}/{owner["total"]} |')
    lines+=['','Pooled is micro F1 from summed TP/system/gold, not the average of type F1. See `summary.json` for each type P/R/F1 and denominators.',
        'Prediction costs and judge costs are separate. With shared_s1_s2, S2 prediction costs exclude reused extraction/merge.',
        'B0 at this commit has the current one-shot prompt; it is not the teammate’s 414a31f arm. S1/S2 are the matched-content context comparison.',
        'An empty P2 clarification sidecar is not proof of full evidence verification. Relation accuracy uses matched source items and is reported separately from extraction F1.','']
    write_text(out/'REPORT.md','\n'.join(lines));return result

def review_sample(jobs,rows,out):
    verdicts={(r['meeting_id'],r['mode'],v['id']):v['verdict'] for r in rows for v in r.get('semantic',{}).get('support',[])}
    sample=[]
    for job in jobs:
        for item in job['record']:
            if item['type']=='abstract':continue
            sample.append({'meeting_id':job['meeting_id'],'mode':job['mode'],'item':item,
                'judge':verdicts.get((job['meeting_id'],job['mode'],item['id'])),'human_statement':None,
                'human_owner':None,'human_deadline':None,'human_relation':None,'human_notes':''})
    sample=sorted(sample,key=lambda r:digest([0,r['meeting_id'],r['mode'],r['item']['id']]))[:30]
    write_json(out/'human_review_template.json',{'seed':0,'sampling':'sha256 order, all predicted non-abstract items; up to 30',
        'status':'NOT_REVIEWED','rows':sample})
    lines=['# Human evidence review','', 'Fixed sample, not a completed human evaluation. Use the saved prediction references and transcript to assess each claim.','']
    for row in sample:
        it=row['item'];lines += [f'## {row["mode"]} / {it["id"]}',it['text'],
            '- Evidence: '+', '.join(r['locator'] for r in it['reference']),'- Judge: '+json.dumps(row['judge']),
            '- Human statement / owner / deadline / relation: ','- Notes: ','']
    write_text(out/'human_review_template.md','\n'.join(lines))

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    source=ap.add_mutually_exclusive_group(required=True);source.add_argument('--run',type=Path);source.add_argument('--saved-dev',type=Path)
    ap.add_argument('--gold',type=Path);ap.add_argument('--series',nargs='+');ap.add_argument('--modes',nargs='+',choices=['b0','s1','s2'],default=['b0','s1','s2'])
    ap.add_argument('--layout',choices=['auto','native','series'],default='auto');ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--online',action='store_true');ap.add_argument('--offline',action='store_true')
    ap.add_argument('--check-only',action='store_true');ap.add_argument('--allow-heldout',action='store_true')
    ap.add_argument('--semantics',choices=['none','support','summary','all'],default='none')
    ap.add_argument('--votes-from',type=Path,help='Reuse this scorer’s exact model/protocol/input-checked caches from an earlier attempt')
    args=ap.parse_args(argv)
    if args.online and args.offline:ap.error('Choose online or offline, not both.')
    if len(set(args.modes))!=len(args.modes):ap.error('Duplicate mode.')
    # Resolve scope before any gold/prediction read or runtime/judge construction.
    if not args.saved_dev:gate(args.series,args.allow_heldout)
    out=args.out.resolve()
    if out.exists():raise ScoringError('Never overwrite a scoring attempt. Choose a new --out.')
    study,p,old=frozen_protocol();jobs,generation=prepare_jobs(args,study)
    if args.saved_dev:
        legacy=generation['legacy_protocol']
        for name in ('votes','judge','match_system','match_schema','fewshot_excluded_ids'):
            if legacy[name]!=p[name]:raise ScoringError('Saved scoring protocol differs: '+name)
    if args.votes_from:
        verify_manifest(args.votes_from)
        previous=c.load(args.votes_from/'plan.json')
        if previous['protocol_digest']!=digest(p):raise ScoringError('Prior judge cache protocol differs.')
    out.mkdir(parents=True)
    plan={'format_version':1,'created_at':c.utc(),'status':'PREFLIGHT','scope':'HELD_OUT_FINAL' if any(j['meeting_id'][:-1] in HELDOUT for j in jobs) else 'DEVELOPMENT_VALIDATION',
        'protocol':p,'protocol_digest':digest(p),'runtime_reference_commit':study['commit'],
        'online':args.online,'semantics':args.semantics,'generation':generation,
        'modes':sorted({j['mode'] for j in jobs}),'jobs':[]}
    rows=[]
    try:
        for job in jobs:
            mid=job['meeting_id'];job['semantics']=args.semantics
            if job['execution_kind']=='NATIVE_PREDICTION':job['record'],job['metadata']=load_native(job['prediction'],mid,job['mode'],study)
            else:job['record']=c.load(job['prediction']);validate_records(job['record'],mid)
            job['gold_items']=c.load(job['gold'])
            validate_gold(job['gold_items'],mid)
            key=mid+'.'+job['mode']
            write_json(out/'inputs'/(key+'.prediction.json'),job['record']);write_json(out/'inputs'/(key+'.gold.json'),job['gold_items'])
            plan['jobs'].append({'meeting_id':mid,'mode':job['mode'],'prediction':str(job['prediction'].resolve()),
                'prediction_sha256':sha256(job['prediction']),'gold':str(job['gold'].resolve()),'gold_sha256':sha256(job['gold']),
                'execution_kind':job['execution_kind'],'metadata_sha256':sha256(job['prediction'].with_suffix('.run.json')) if job.get('metadata') else None})
        for job in jobs:
            known={r['id'] for j in jobs if j['mode']==job['mode'] and j['meeting_id'][:-1]==job['meeting_id'][:-1] and j['meeting_id']<job['meeting_id'] for r in j['record'] if r['type']!='abstract'}
            if any(link['id'] not in known for r in job['record'] for link in r.get('related_items',[])):
                raise ScoringError('Prediction refers to a missing earlier item in its own mode.')
        plan['status']='PREFLIGHT_PASSED';write_json(out/'plan.json',plan)
        if args.check_only:
            print(json.dumps({'status':'PREFLIGHT_PASSED','jobs':len(jobs),'api_calls':0,'scope':plan['scope']}));return 0
        if args.online:
            from run_frozen import verify
            verify()  # same real model/config; no alternate judge
        for job in jobs:
            value=score_job(job,p,out,args.online,old,args.votes_from);rows.append(value)
            print(json.dumps({'meeting_id':job['meeting_id'],'mode':job['mode'],'status':value['status'],
                              'judge_calls':value['runtime_control']['attempted_calls']}),flush=True)
            if value['status']!='COMPLETED':break
        if args.saved_dev and len(rows)==len(jobs) and all(r['status']=='COMPLETED' for r in rows):
            expected=c.load(args.saved_dev/'comparison.json')['aggregate'];actual=aggregate(rows)
            actual={kind:{k:v for k,v in values.items() if k!='eligible_meetings'} for kind,values in actual.items()}
            if actual!=expected:raise ScoringError('Aggregate differs from saved development reference.')
            plan['saved_reference_validation']={'status':'PASSED','per_meeting_exact_matches':len(rows),
                'all_type_and_pooled_metrics_exact':True,'reference_sha256':sha256(args.saved_dev/'comparison.json')}
        result=summarize(rows,jobs,plan,out);review_sample(jobs,rows,out)
        plan['status']=result['status'];write_json(out/'plan.json',plan)
        return 0 if result['status']=='COMPLETED' else 1
    except Exception as exc:
        plan.update(status='PREFLIGHT_FAILED' if plan['status']=='PREFLIGHT' else 'SCORING_FAILED',failure={'type':type(exc).__name__,
            'reason':str(exc) if isinstance(exc,ScoringError) else 'input_or_scoring_error; see error type'})
        write_json(out/'plan.json',plan)
        print(json.dumps({'status':plan['status'],'failure':plan['failure']}),flush=True);return 1
    finally:
        write_json(out/'manifest.json',{'artifact_sha256':{p.relative_to(out).as_posix():sha256(p) for p in sorted(out.rglob('*'))
            if p.is_file() and p!=out/'manifest.json'}})

if __name__=='__main__':
    try:sys.exit(main())
    except ScoringError as exc:print(json.dumps({'status':'REFUSED','reason':str(exc)}));sys.exit(2)
