"""Derive a four-meeting driver; never edit the retained original cohort or its driver."""
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'s2_dev16/campaign.py'
def replace(text,before,after,count=1):
    assert text.count(before)==count,(before,text.count(before))
    return text.replace(before,after)
def main():
    p=ROOT/'campaign.py';assert not p.exists()
    s=OLD.read_text(encoding='utf-8')
    s=replace(s,'One cold S2 campaign on the 16 SB meetings.','Cold TS3006 a-to-d after explicit duplicate metadata repair.')
    s=replace(s,"SERIES=('ES2011','IS1008','TS3004','TS3006')","SERIES=('TS3006',)")
    s=replace(s,"COMMIT='787d88e34d803a3bcafa4817720634f2dd74b50e'", "COMMIT='787d88e34d803a3bcafa4817720634f2dd74b50e'\nOLD=REPO/'agent/results/s2_dev16/787d88e-v1'")
    s=replace(s,"assert not identity['code']['dirty_source_files']",
        "assert {row.split()[-1] for row in identity['code']['dirty_source_files']}=={'agent/src/mta/item_selection.py'}",2)
    s=replace(s,"expected=copy.deepcopy(load(REPO/'agent/results/p2_protocol_followup/fixed-v5/study.json')['runtime']['config'])\n    expected['P2_PROTOCOL_VERSION']=19",
        "expected=load(OLD/'study.json')['runtime']['config']")
    s=replace(s,"assert identity['model']['name']=='@30875-fall26-openai/gpt-5-nano'",
        "assert identity['model']==load(OLD/'study.json')['runtime']['model']")
    s=replace(s,"        assert blob.replace(b'\\r\\n',b'\\n')==p.read_bytes().replace(b'\\r\\n',b'\\n'),rel\n        if blob!=p.read_bytes():eol.append(rel)",
        "        if rel=='agent/src/mta/item_selection.py':\n            assert blob.replace(b'\\r\\n',b'\\n')!=p.read_bytes().replace(b'\\r\\n',b'\\n')\n        else:\n            assert blob.replace(b'\\r\\n',b'\\n')==p.read_bytes().replace(b'\\r\\n',b'\\n'),rel\n            if blob!=p.read_bytes():eol.append(rel)")
    s=replace(s,"p=config.TRANSCRIPTS/(mid+'.txt');out=root/'inputs/transcripts'/p.name",
        "p=OLD/'inputs/transcripts'/(mid+'.txt');out=root/'inputs/transcripts'/p.name")
    s=replace(s,"for p in (config.PARTICIPANTS,config.SCENARIO):",
        "for p in (OLD/'inputs/participants.json',OLD/'inputs/scenario_text.txt'):")
    s=replace(s,"    shutil.copyfile(Path(__file__),root/'driver.py')",'''    for rel,digest in copied.items():assert digest==load(OLD/'study.json')['input_sha256'][rel],rel
    proof=Path(__file__).resolve().parent
    replay=load(proof/'replay.json');assert replay['result']=='PASSED' and replay['actual_new_api_calls']==0
    assert replay['after_source_sha256']==sha(REPO/'agent/src/mta/item_selection.py')
    test_line=next(line for line in (proof/'tests.log').read_text(encoding='utf-8').splitlines() if 'passed in' in line)
    assert int(test_line.split()[0])==400
    (root/'offline').mkdir()
    for name in ('tests.log','replay.json','before.select.trace.jsonl','after.select.trace.jsonl','capture.json','before.json'):
        shutil.copyfile(proof/name,root/'offline'/name)
    (root/'source.patch').write_text(git('diff','--','agent/src/mta/item_selection.py')+'\\n',encoding='utf-8')
    save(root/'tests.json',{'passed':400,'seconds':float(test_line.split()[3].rstrip('s')),
        'actual_api_calls':0,'source_sha256':source,
        'test_sha256':{p.relative_to(REPO).as_posix():sha(p) for p in (REPO/'agent/tests').glob('*.py')},
        'fixture_sha256':sha(REPO/'agent/tests/fixtures/selection_missing_reason.json')})
    shutil.copyfile(Path(__file__),root/'driver.py')''')
    s=replace(s,"previous=load(REPO/'agent/results/p2_protocol_followup/before.json')['protected_files']",
        "previous=load(proof/'before.json')['protected_files']")
    s=replace(s,"'runtime':identity,'source_sha256':source,'source_eol_only_differences_from_commit':eol,",
        "'runtime':identity,'source_sha256':source,'source_eol_only_differences_from_commit':eol,\n        'source_patch_paths':['agent/src/mta/item_selection.py'],'source_patch_sha256':sha(root/'source.patch'),\n        'original_cohort':str(OLD),'same_model_and_config_as_original':True,'offline_replay_passed':True,")
    s=replace(s,"scheduled=16)","scheduled=len(MEETINGS))")
    p.write_text(s,encoding='utf-8')
    print('New four-meeting driver written; original cohort unchanged')

if __name__=='__main__':main()
