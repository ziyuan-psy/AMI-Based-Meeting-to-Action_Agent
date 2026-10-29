"""One bounded reproduction of the saved HTTP 400; sanitize error details, never credentials."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parent
REPO=next(p for p in ROOT.parents if (p/'agent/src/mta/p2.py').is_file())
sys.path.insert(0,str(REPO/'agent/src'))
from mta.control import RunControl,Limits
from mta.llm import LLM
from mta.trace import StageTrace

path=ROOT/'http400_diagnostic.json'
if path.exists():raise FileExistsError(path)
requests=[json.loads(line) for line in (ROOT/'fixed-v3/IS1008a.requests.jsonl').read_text(encoding='utf-8').splitlines()]
request=next(row for row in reversed(requests) if row['event']=='model_request')
trace=StageTrace(ROOT/'http400_diagnostic.trace.jsonl')
control=RunControl(replace(Limits.configured(),calls=1,retries=0),trace=trace)
model=LLM();result={}
try:
    response=model.scoped(control).create(request['system'],request['messages'],request['tools'],request['tool_choice'],
        max_tokens=request['max_tokens'],effort=request['effort'],stage=request['stage'])
    result={'status':'NO_LONGER_REPRODUCED','usage':model.usage_snapshot()}
except Exception as error:
    cause=error;seen=set();result={'status':'REPRODUCED','error_type':type(error).__name__}
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause));body=getattr(cause,'body',None)
        if isinstance(body,dict):
            body=body.get('error',body)
            if isinstance(body,dict):
                details={k:body[k] for k in ('code','type','param','message') if isinstance(body.get(k),(str,int,float))}
                for k,v in list(details.items()):
                    if isinstance(v,str):
                        for name,secret in os.environ.items():
                            if re.search(r'KEY|TOKEN|SECRET|PASSWORD',name,re.I) and len(secret)>=8:v=v.replace(secret,'[redacted]')
                        v=re.sub(r'sk[-_][A-Za-z0-9_-]{8,}|Bearer\s+\S+','[redacted]',v,flags=re.I)
                        if re.search(r'api.?key|authorization|credential|password|secret',v,re.I):v='[authentication detail redacted]'
                        details[k]=v[:1800]
                result.update(http_status=getattr(cause,'status_code',None),details=details)
                break
        cause=cause.__cause__ or cause.__context__
finally:
    control.seal();trace.finish(stage='diagnostic',event='finished')
    result['control']=control.snapshot();path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='control'},ensure_ascii=False))
