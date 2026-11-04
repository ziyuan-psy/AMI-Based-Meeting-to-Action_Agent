"""Demo: show how an item changes between meetings, from real run outputs (no model calls).

Usage:  python scripts/demo.py TS3006c [--run runs/mvp] [--item N]
Prints/writes docs/DEMO_<meeting>.md: earlier record, current evidence, the agent's choices, the S1 result for the same
item (no earlier-meeting context), and the resulting action table.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mta import config  # noqa: E402
from mta.store import Store, tokenize  # noqa: E402

NL = chr(10)


def jac(a: str, b: str) -> float:
    x, y = set(tokenize(a)), set(tokenize(b))
    return len(x & y) / len(x | y) if x | y else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("meeting")
    ap.add_argument("--run", type=Path, default=config.RUNS / "mvp")
    ap.add_argument("--item", type=int, help="1-based item number in the record (default: first action with related_items)")
    a = ap.parse_args()
    m = a.meeting
    series, order = m[:-1], "abcd".index(m[-1]) + 1
    s2 = json.loads((a.run / "s2" / f"{m}.json").read_text(encoding="utf-8"))
    s1 = json.loads((a.run / "s1" / f"{m}.json").read_text(encoding="utf-8"))
    earlier = {}
    for c in "abcd"[: order - 1]:
        for x in json.loads((a.run / "s2" / f"{series}{c}.json").read_text(encoding="utf-8")):
            earlier[x["id"]] = x

    if a.item:
        item = next(x for x in s2 if x["id"].endswith(f".s.{a.item}"))
    else:
        item = next((x for x in s2 if x["type"] == "actions" and x.get("related_items")),
                    next((x for x in s2 if x.get("related_items")), None))
    if item is None:
        print("no item with related_items in this meeting; pass --item N")
        return 1

    store = Store(m, allow_prior=True, run_dir=a.run / "s2")
    n = int(item["id"].rsplit(".", 1)[1]) - 1  # trace keys use the 0-based position
    trace = [json.loads(line) for line in (a.run / "s2" / f"{m}.trace.jsonl").read_text(encoding="utf-8").splitlines()]
    steps = [t for t in trace if t.get("item") == f"{m}#{n}"]
    twin = max((x for x in s1 if x["type"] == item["type"]), key=lambda x: jac(x["text"], item["text"]))

    def owner(x):
        return ", ".join(x["owner"]) if x.get("owner") else "-"

    out = [f"# Demo: {m} (series {series})", "",
           "Real output of the workflow. The agent's choices come from the decision log; nothing here is hand-written.", "",
           "## 1. Earlier record (what the system had recorded before this meeting)", ""]
    for r in item["related_items"]:
        e = earlier.get(r["id"], {})
        out += [f"- `{r['id']}` ({e.get('type', '?')}): {e.get('text', '(not found)')}",
                f"  - owner: {owner(e)}; deadline: {e.get('deadline') or '-'}; status: {e.get('status', '-')}"]
    out += ["", "## 2. Item in this meeting", "", f"- {item['text']}",
            f"  - owner: {owner(item)} ({item.get('owner_basis', '-')}); deadline: {item.get('deadline') or '-'};"
            f" status: {item['status']}", "", "Evidence lines (current meeting):", ""]
    for r in item["reference"]:
        if r["source_type"] == "dialogue" and r["locator"].split(".")[0] == m:
            mm = store.meeting(m)
            da = r["locator"]
            if da in mm.by_id:
                x = mm.acts[mm.by_id[da]]
                out.append(f"- `{da.split('.', 1)[1]}` {x['attributes']['role']}: {x['text']}")
    out += ["", "## 3. What the agent chose to do (decision log)", ""]
    if not steps:
        out.append("- (item was not flagged: no follow-up lookups)")
    for t in steps:
        args = json.dumps(t.get("args", {}), ensure_ascii=False)
        args = args if len(args) < 150 else args[:147] + "..."
        out.append(f"{t['step']}. flags {t['flags']} -> `{t['action']}` {args} -> {t.get('outcome')}"
                   + (f", new info: {t['new_info']}" if "new_info" in t else ""))
    out += ["", "## 4. Same item without earlier-meeting context (S1)", "",
            f"- {twin['text']}", f"  - related items: {twin.get('related_items') or 'none'};"
            f" prior_context_needed: {twin.get('prior_context_needed')}",
            "", "## 5. Resulting action table (S2)", "",
            "| ID | Action | Owner | Deadline | Status | Change vs earlier |", "|---|---|---|---|---|---|"]
    for x in s2:
        if x["type"] != "actions":
            continue
        rel = "; ".join(f"{r['relation']} {r['id'].split('.', 1)[1]}" for r in x["related_items"]) or "-"
        out.append(f"| {x['id'].rsplit('.', 1)[1]} | {x['text'].replace('|', '/')} | {owner(x)} | {x.get('deadline') or '-'} |"
                   f" {x['status']} | {rel} |")
    text = NL.join(out) + NL
    dest = ROOT / "docs" / f"DEMO_{m}.md"
    dest.write_text(text, encoding="utf-8")
    print(text)
    print(f"written to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
