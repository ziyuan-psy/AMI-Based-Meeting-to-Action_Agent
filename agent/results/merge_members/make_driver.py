"""Derive a separate cold-run driver without editing prior regression records."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
text = (HERE.parent / 'select_reason/campaign.py').read_text(encoding='utf-8')

def replace(old, new, count=1):
    global text
    assert text.count(old) == count, (old, text.count(old))
    text = text.replace(old, new)

replace('Cold TS3006 a-to-d after explicit duplicate metadata repair.',
        'Cold TS3006 a-to-d after rejecting malformed consolidation members.')
replace("OLD=REPO/'agent/results/s2_dev16/787d88e-v1'",
        "OLD=REPO/'agent/results/select_reason/ts3006-v1'\nPATCH_PATHS=['agent/src/mta/item_selection.py','agent/src/mta/extract.py']")
replace("=={'agent/src/mta/item_selection.py'}", '==set(PATCH_PATHS)', 2)
replace("if rel=='agent/src/mta/item_selection.py':", 'if rel in PATCH_PATHS:')
replace("    copied={}\n", "    assert source['agent/src/mta/item_selection.py']==load(OLD/'study.json')['source_sha256']['agent/src/mta/item_selection.py'],'Prior selection fix changed'\n    copied={}\n")
replace("replay['after_source_sha256']==sha(REPO/'agent/src/mta/item_selection.py')",
        "replay['after_source_sha256']==sha(REPO/'agent/src/mta/extract.py')")
replace('==400', '==428')
replace("'before.select.trace.jsonl','after.select.trace.jsonl'",
        "'17.merge.trace.jsonl','18.merge.trace.jsonl','19.merge.trace.jsonl'")
replace("git('diff','--','agent/src/mta/item_selection.py')", "git('diff','--',*PATCH_PATHS)")
replace("{'passed':400,", "{'passed':428,")
replace("'fixture_sha256':sha(REPO/'agent/tests/fixtures/selection_missing_reason.json')",
        "'fixture_sha256':{name:sha(REPO/'agent/tests/fixtures'/name) for name in ('selection_missing_reason.json','consolidation_bad_members.json')}")
replace("'source_patch_paths':['agent/src/mta/item_selection.py']", "'source_patch_paths':PATCH_PATHS")
(HERE/'campaign.py').write_text(text, encoding='utf-8')
print('Created independent four-meeting driver; prior driver unchanged.')
