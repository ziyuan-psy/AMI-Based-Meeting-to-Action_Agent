"""Read-only compact progress; tolerates an unfinished last JSONL write."""
from collections import Counter
import json
from pathlib import Path

root=Path(__file__).resolve().parent/'ts3006-v1'
state=json.loads((root/'state.json').read_text(encoding='utf-8'))
for row in state['meetings']:
    path=root/'run/s2'/(row['meeting_id']+'.stages.jsonl')
    if not path.exists():continue
    events=[]
    for line in path.read_text(encoding='utf-8').splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    merged=[e for e in events if e.get('event')=='attempt' and 'sample' in e and e.get('stage') in ('consolidate','decision_consolidate')]
    rejects=Counter(r['reason'] for e in merged for d in e['events'] if d.get('event')=='merged' for r in d.get('rejected',[]))
    print(json.dumps({'meeting_id':row['meeting_id'],'status':row['status'],'merge_responses':len(merged),
        'unusable':sum(not e.get('items') for e in merged),'rejections':dict(rejects)},ensure_ascii=False))
