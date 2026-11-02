"""Evidence-support check: does the cited dialogue actually support the item, its owner and its deadline?

A reference that exists does not prove support. This judge sees only the claim and the cited lines (not the gold, not
which system produced the item). It is an LLM judgement: spot-check the exported sample by hand before trusting it.

Usage:  python -m mta.support TS3006 --run runs/mvp --mode s2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from . import config
from .llm import LLM
from .store import Store

NL = chr(10)
LEVELS = ["supported", "partial", "unsupported"]
SYSTEM = """You check whether cited meeting dialogue supports a recorded claim. Judge ONLY from the cited lines.
For each item decide:
- statement: do the lines support that this commitment / decision / problem was stated or agreed? (supported / partial / unsupported)
- owner (actions with an owner only): do the lines show that exactly this person or role is assigned? (supported / partial / unsupported)
- deadline (actions with a deadline only): do the lines support this deadline wording? (supported / partial / unsupported)
Use "partial" when the lines support only part of the claim, or support it only loosely. Speakers are shown as ROLE(PARTICIPANT_ID)."""
SCHEMA = {"type": "object", "properties": {"verdicts": {"type": "array", "items": {
    "type": "object", "properties": {
        "item": {"type": "integer"},
        "statement": {"enum": LEVELS},
        "owner": {"enum": LEVELS + ["na"]},
        "deadline": {"enum": LEVELS + ["na"]},
        "note": {"type": "string"}},
    "required": ["item", "statement", "owner", "deadline"]}}}, "required": ["verdicts"]}


def cited_lines(store: Store, item: dict, limit: int = 10) -> list[str]:
    out = []
    for r in item["reference"]:
        if r["source_type"] != "dialogue":
            continue
        da = r["locator"].split(" ")[0]
        mid = da.split(".")[0]
        m = store.meeting(mid)
        if da in m.by_id:
            a = m.acts[m.by_id[da]]
            out.append(f"[{da.split('.', 1)[1]}] {a['attributes']['role']}({a['attributes']['participant']}): {a['text']}")
    return out[:limit]


def claim_text(item: dict) -> str:
    s = f"({item['type']}) {item['text']}"
    if item["type"] == "actions":
        s += f" | owner: {', '.join(item['owner']) if item['owner'] else 'none'}"
        s += f" | deadline: {item['deadline'] or 'none'}"
    return s


def judge_meeting(llm: LLM, store: Store, items: list[dict], cache: dict) -> list[dict]:
    block = []
    for i, it in enumerate(items):
        lines = cited_lines(store, it) or ["(no dialogue cited)"]
        block.append(f"ITEM {i}: {claim_text(it)}{NL}CITED LINES:{NL}" + NL.join(lines))
    user = (NL + NL).join(block)
    key = hashlib.sha256(user.encode()).hexdigest()[:20]
    make = lambda: llm.forced_tool(SYSTEM, user, "submit_verdicts", SCHEMA)["verdicts"]
    if hasattr(cache, "compute"):
        verdicts = cache.compute(key, make)  # one judgement per key, even when modes are scored in parallel
    else:
        if key not in cache:
            cache[key] = make()
        verdicts = cache[key]
    by_item = {v["item"]: v for v in verdicts if isinstance(v.get("item"), int)}
    return [by_item.get(i) for i in range(len(items))]


def run(series: str, run_dir: Path, mode: str, llm, sample_n: int = 30, seed: int = 0) -> dict:
    """`llm`: the evaluation judge (mta.judge.get_judge). Verdicts live in the shared judge cache, keyed by exactly
    what the judge sees (claims + cited lines), so unchanged items are never judged twice."""
    from .judge import shared_cache
    cache = shared_cache("support_cache", llm)
    rates = defaultdict(Counter)
    rows = []
    for c in "abcd":
        m = f"{series}{c}"
        f = run_dir / mode / f"{m}.json"
        if not f.exists():
            continue
        items = [i for i in json.loads(f.read_text(encoding="utf-8")) if i["type"] != "abstract"]
        if not items:
            continue
        store = Store(m, allow_prior=True, run_dir=run_dir / mode)
        for it, v in zip(items, judge_meeting(llm, store, items, cache)):
            if not v:
                continue
            rates[f"{it['type']}.statement"][v["statement"]] += 1
            if it["type"] == "actions" and it["owner"] and v["owner"] in LEVELS:
                rates["actions.owner"][v["owner"]] += 1
            if it["type"] == "actions" and it["deadline"] and v["deadline"] in LEVELS:
                rates["actions.deadline"][v["deadline"]] += 1
            rows.append({"item": it["id"], "claim": claim_text(it), "cited": cited_lines(store, it, 5),
                         "judge": {k: v[k] for k in ("statement", "owner", "deadline")}, "note": v.get("note", "")})

    def summarise(c: Counter) -> dict:
        n = sum(c.values())
        return {"n": n, **{k: c.get(k, 0) for k in LEVELS}, "supported_rate": round(c.get("supported", 0) / n, 3) if n else None,
                "supported_or_partial_rate": round((c.get("supported", 0) + c.get("partial", 0)) / n, 3) if n else None}

    result = {"series": series, "mode": mode, "support": {k: summarise(v) for k, v in sorted(rates.items())},
              "note": "LLM judge; check the exported sample by hand"}
    random.Random(seed).shuffle(rows)
    sample = rows[:sample_n]
    out_md = run_dir / mode / f"support_sample_{series}.md"
    lines = [f"# Evidence-support sample ({len(sample)} items) - fill in the human column", ""]
    for r in sample:
        lines += [f"## {r['item']}", f"- claim: {r['claim']}", "- cited:"] + [f"  - {x}" for x in r["cited"]]
        lines += [f"- judge: {r['judge']}  {r['note']}", "- human (supported / partial / unsupported): ", ""]
    out_md.write_text(NL.join(lines), encoding="utf-8")
    (run_dir / mode / f"support_{series}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--run", type=Path, default=config.RUNS / "mvp")
    ap.add_argument("--mode", default="s2")
    ap.add_argument("--sample", type=int, default=30)
    a = ap.parse_args(argv)
    if a.series in config.SC_SERIES:
        raise SystemExit("held-out series: run only after the design is frozen")
    from .judge import get_judge
    r = run(a.series, a.run, a.mode, get_judge(), a.sample)
    print(f"{a.series} mode={a.mode}")
    for k, v in r["support"].items():
        print(f"  {k:22s} n={v['n']:3d} supported={v['supported']} partial={v['partial']} unsupported={v['unsupported']}"
              f"  supported_rate={v['supported_rate']}")
    print(f"sample for human check: {a.run / a.mode / ('support_sample_' + a.series + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
