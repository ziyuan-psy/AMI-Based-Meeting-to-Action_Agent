"""Verify transferred acceptance artifacts using only Python's standard library; no API/data reads."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
errors=[]
for rel,digest in manifest['artifact_sha256'].items():
    path=(root/rel).resolve()
    if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        errors.append(rel)
print(json.dumps({'status':'PASSED' if not errors else 'FAILED','artifacts_checked':len(manifest['artifact_sha256']),
                  'errors':errors,'api_calls':0,'heldout_opened':False}))
raise SystemExit(bool(errors))
