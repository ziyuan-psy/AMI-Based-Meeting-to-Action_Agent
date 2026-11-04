"""Offline evaluation against the revised gold (schema 0.5.0). Gold is read only here, never by the runtime.

Alignment is deterministic and one-to-one per item type: score = 0.6 * evidence overlap + 0.4 * text overlap.
It is a proxy for "same item"; use --verbose to read the matches and unmatched items.

Usage:  python -m mta.evaluate TS3006 --run runs/dev --mode s2 [--gold <dir>] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from . import config

GOLD_DIR = Path(os.environ.get("MTA_GOLD_DIR", r"D:\meeting_gold\AMI-Based-Meeting-to-Action_Agent\revised_gold_output"))
TYPES = ("actions", "decisions", "problems")
STOP = set("the a an of to and in for on will be is are with by that this it as at from or we they their should "
           "would can have has not no".split())
MATCH_THRESHOLD = 0.25


def toks(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9']+", text.lower()) if t not in STOP}


def evidence(item: dict) -> set[str]:
    return {r["locator"].split(" ")[0] for r in item.get("reference", []) if r["source_type"] == "dialogue"}


def sim(s: dict, g: dict) -> float:
    es, eg = evidence(s), evidence(g)
    ev = len(es & eg) / min(len(es), len(eg)) if es and eg else 0.0
    ts, tg = toks(s["text"]), toks(g["text"])
    tx = len(ts & tg) / len(ts | tg) if ts | tg else 0.0
    return 0.6 * ev + 0.4 * tx if (ev > 0 or tx >= 0.3) else 0.0


def align(sys_items: list[dict], gold_items: list[dict]) -> dict[str, str]:
    """Greedy one-to-one matching, best scores first. Returns {system id: gold id}."""
    pairs = sorted(((sim(s, g), s["id"], g["id"]) for s in sys_items for g in gold_items), reverse=True)
    used_s, used_g, out = set(), set(), {}
    for score, sid, gid in pairs:
        if score < MATCH_THRESHOLD:
            break
        if sid not in used_s and gid not in used_g:
            out[sid] = gid
            used_s.add(sid)
            used_g.add(gid)
    return out


MATCH_SYSTEM = """You compare two lists of items from the same meeting record: SYSTEM items and GOLD items.
Match each system item to at most one gold item that records the same commitment, decision or problem.
Paraphrase is fine; for actions it must be the same person doing essentially the same task. A system item that
is only loosely related, much narrower, or about something else is NOT a match. Matching is one-to-one."""
MATCH_SCHEMA = {"type": "object", "properties": {"pairs": {"type": "array", "items": {
    "type": "object", "properties": {"sys": {"type": "integer"}, "gold": {"type": "integer"}},
    "required": ["sys", "gold"]}}}, "required": ["pairs"]}


def make_llm_matcher(llm, cache=None, votes: int | None = None):
    """LLM alignment by majority vote, used instead of the heuristic when --judge is set.

    The judge aligns the same two lists `votes` times; a pair counts when a majority of the runs give it, and the
    accepted pairs are then made one-to-one (most votes first). Every run is cached by the item texts, in the shared
    judge cache by default, so identical lists are never judged twice and raising `votes` only adds the missing runs.
    `cache`: a dict-like store (mta.judge.JsonCache); default the shared "align_votes" cache of this judge.
    """
    import contextlib
    import hashlib
    from collections import Counter
    from .judge import shared_cache
    store = shared_cache("align_votes", llm) if cache is None else cache
    votes = votes or config.EVAL_VOTES
    need = votes // 2 + 1

    def matcher(ss: list[dict], gg: list[dict]) -> dict[str, str]:
        if not ss or not gg:
            return {}
        key = hashlib.sha256(json.dumps([[s["text"] for s in ss], [g["text"] for g in gg]]).encode()).hexdigest()[:20]
        # Another thread (e.g. the other mode, with the same items) may be judging the same lists: wait for it.
        with (store.lock_for(key) if hasattr(store, "lock_for") else contextlib.nullcontext()):
            runs = list(store.get_value(key, []) if hasattr(store, "get_value") else store.get(key, []))
            if len(runs) < votes:
                sys_lines = [f"[{i}] {s['text']}" for i, s in enumerate(ss)]
                gold_lines = [f"[{j}] {g['text']}" for j, g in enumerate(gg)]
                user = "SYSTEM items:\n" + "\n".join(sys_lines) + "\n\nGOLD items:\n" + "\n".join(gold_lines)
                while len(runs) < votes:
                    runs.append(llm.forced_tool(MATCH_SYSTEM, user, "submit_matches", MATCH_SCHEMA).get("pairs") or [])
                    store[key] = list(runs)
        count = Counter()
        for pairs in runs[:votes]:
            seen = set()
            for p in pairs:
                i, j = (p.get("sys"), p.get("gold")) if isinstance(p, dict) else (None, None)
                if isinstance(i, int) and isinstance(j, int) and 0 <= i < len(ss) and 0 <= j < len(gg) and (i, j) not in seen:
                    seen.add((i, j))
                    count[(i, j)] += 1
        out, used = {}, set()
        for (i, j), c in sorted(count.items(), key=lambda kv: (-kv[1], kv[0])):
            if c < need:
                break
            if ss[i]["id"] not in out and j not in used:
                out[ss[i]["id"]] = gg[j]["id"]
                used.add(j)
        return out
    return matcher


LINK_SYSTEM = """You compare two lists of items from the same meeting record: SYSTEM items and GOLD items.
Link a system item to a gold item when one of these holds:
- same: both record the same commitment, decision or problem; only the wording differs.
- split: the system item is a substantial part of the gold item (the gold item has more to it).
- merge: the system item covers this gold item together with one or more other gold items. Give one merge link for
  every gold item it covers.
Paraphrase is fine; for actions it must be the same person doing essentially the same task. Items that are only on
the same topic but say something different are NOT linked. A system item may link to several gold items and a gold
item may be linked from several system items; system items with no link are left out."""
LINK_SCHEMA = {"type": "object", "properties": {"links": {"type": "array", "items": {
    "type": "object", "properties": {"sys": {"type": "integer"}, "gold": {"type": "integer"},
                                     "relation": {"enum": ["same", "split", "merge"]}},
    "required": ["sys", "gold", "relation"]}}}, "required": ["links"]}
RELATIONS = ("same", "split", "merge")


def vote_links(runs: list, n_sys: int, n_gold: int, votes: int) -> list[tuple[int, int, str]]:
    """Majority vote over judge runs. A (sys, gold) link counts when a majority of runs give it (any relation).
    Its relation is "same" when a majority of runs say same; otherwise the most frequent other relation."""
    from collections import Counter
    need = votes // 2 + 1
    linked, rel = Counter(), {}
    for links in runs[:votes]:
        seen = set()
        for p in links:
            if not isinstance(p, dict):
                continue
            i, j, r = p.get("sys"), p.get("gold"), p.get("relation")
            if (isinstance(i, int) and isinstance(j, int) and 0 <= i < n_sys and 0 <= j < n_gold
                    and r in RELATIONS and (i, j) not in seen):
                seen.add((i, j))
                linked[(i, j)] += 1
                rel.setdefault((i, j), Counter())[r] += 1
    out = []
    for (i, j), c in sorted(linked.items()):
        if c < need:
            continue
        r = rel[(i, j)]
        label = "same" if r["same"] >= need else max(("split", "merge"), key=lambda x: (r[x], x == "split"))
        out.append((i, j, label))
    return out


def make_tolerant_matcher(llm, cache=None, votes: int | None = None):
    """Split/merge-tolerant alignment: many-to-many links labelled same / split / merge, by majority vote.

    Returns matcher(ss, gg) -> list of (system id, gold id, relation). Runs are cached like make_llm_matcher's, in the
    shared "link_votes" cache by default. Score the links with `tolerant_prf`.
    """
    import contextlib
    import hashlib
    from .judge import shared_cache
    store = shared_cache("link_votes", llm) if cache is None else cache
    votes = votes or config.EVAL_VOTES

    def matcher(ss: list[dict], gg: list[dict]) -> list[tuple[str, str, str]]:
        if not ss or not gg:
            return []
        key = hashlib.sha256(json.dumps([[s["text"] for s in ss], [g["text"] for g in gg]]).encode()).hexdigest()[:20]
        with (store.lock_for(key) if hasattr(store, "lock_for") else contextlib.nullcontext()):
            runs = list(store.get_value(key, []) if hasattr(store, "get_value") else store.get(key, []))
            if len(runs) < votes:
                sys_lines = [f"[{i}] {s['text']}" for i, s in enumerate(ss)]
                gold_lines = [f"[{j}] {g['text']}" for j, g in enumerate(gg)]
                user = "SYSTEM items:\n" + "\n".join(sys_lines) + "\n\nGOLD items:\n" + "\n".join(gold_lines)
                while len(runs) < votes:
                    runs.append(llm.forced_tool(LINK_SYSTEM, user, "submit_links", LINK_SCHEMA).get("links") or [])
                    store[key] = list(runs)
        return [(ss[i]["id"], gg[j]["id"], r) for i, j, r in vote_links(runs, len(ss), len(gg), votes)]
    return matcher


def drop_duplicates(links: list[tuple[str, str, str]], sys_order: list[str]) -> tuple[list, list]:
    """A gold item restated in full by several system items ("same" links) keeps only the first system item in
    record order; the other "same" links are removed. Split/merge links are never removed.
    Returns (kept links, removed links)."""
    pos = {sid: k for k, sid in enumerate(sys_order)}
    first = {}
    for sid, gid, r in sorted(links, key=lambda x: pos.get(x[0], len(pos))):
        if r == "same":
            first.setdefault(gid, sid)
    kept, removed = [], []
    for link in links:
        sid, gid, r = link
        (removed if r == "same" and first[gid] != sid else kept).append(link)
    return kept, removed


def tolerant_prf(sys_ids: list[str], gold_ids: list[str], links: list[tuple[str, str, str]]) -> dict:
    """Precision = system items with at least one link / system items; recall = gold items covered by at least one
    system item / gold items. Pure duplicates are removed first (drop_duplicates), so they count as false positives
    unless they also have another link."""
    kept, removed = drop_duplicates(links, sys_ids)
    linked_sys = {s for s, _, _ in kept}
    covered = {g for _, g, _ in kept}
    p = len(linked_sys) / len(sys_ids) if sys_ids else 0.0
    r = len(covered) / len(gold_ids) if gold_ids else 0.0
    rel = {k: sum(1 for _, _, x in kept if x == k) for k in RELATIONS}
    return {"linked_sys": len(linked_sys), "sys": len(sys_ids), "covered_gold": len(covered), "gold": len(gold_ids),
            "precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
            "links": len(kept), "relations": rel, "duplicate_links_removed": len(removed),
            "duplicates_counted_fp": len({s for s, _, _ in removed} - linked_sys), "kept_links": kept}


def prf(tp: int, n_sys: int, n_gold: int) -> dict:
    p = tp / n_sys if n_sys else 0.0
    r = tp / n_gold if n_gold else 0.0
    return {"tp": tp, "sys": n_sys, "gold": n_gold, "precision": round(p, 3), "recall": round(r, 3),
            "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


def deadline_match(s: dict, g: dict) -> bool:
    a, b = s.get("deadline"), g.get("deadline")
    if not a or not b:
        return not a and not b
    ta, tb = toks(a), toks(b)
    return bool(ta & tb) and len(ta & tb) / len(ta | tb) >= 0.34


def evaluate(series: str, run_dir: Path, mode: str, gold_dir: Path = GOLD_DIR, verbose: bool = False,
             matcher=None) -> dict:
    if series in config.SC_SERIES:
        raise SystemExit("held-out series: evaluate only after the design is frozen (use --allow-sc)")
    gold_by_m, sys_by_m, amap = {}, {}, {}
    for c in "abcd":
        m = f"{series}{c}"
        gold_by_m[m] = json.loads((gold_dir / f"{m}.json").read_text(encoding="utf-8"))
        f = run_dir / mode / f"{m}.json"
        sys_by_m[m] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    cnt = {t: [0, 0, 0] for t in TYPES}  # tp, sys, gold
    acc = defaultdict(lambda: [0, 0])    # name -> [correct, total]
    rel = [0, 0, 0]                      # tp, sys pairs, gold pairs  (over matched items)
    prior_slice = [0, 0]                 # items where gold prior_context_needed=True: [flagged correctly, total]
    ev = [0, 0, 0, 0]                    # sys refs in gold, sys refs, gold refs covered, gold refs
    invented = [0, 0]                    # gold deadline null: [system gave one, total]
    per_meeting = {}
    for m in gold_by_m:
        s_items, g_items = sys_by_m[m], gold_by_m[m]
        if s_items is None:
            continue
        mc = {}
        for t in TYPES:
            ss = [i for i in s_items if i["type"] == t]
            gg = [i for i in g_items if i["type"] == t]
            pairs = matcher(ss, gg) if matcher else align(ss, gg)
            amap.update(pairs)
            cnt[t][0] += len(pairs)
            cnt[t][1] += len(ss)
            cnt[t][2] += len(gg)
            mc[t] = prf(len(pairs), len(ss), len(gg))
            sid = {i["id"]: i for i in ss}
            gid = {i["id"]: i for i in gg}
            for s_id, g_id in pairs.items():
                s, g = sid[s_id], gid[g_id]
                acc[f"{t}.status"][1] += 1
                acc[f"{t}.status"][0] += s["status"] == g["status"]
                acc["all.prior_context_needed"][1] += 1
                acc["all.prior_context_needed"][0] += s["prior_context_needed"] == g["prior_context_needed"]
                if g["prior_context_needed"] is True:
                    prior_slice[1] += 1
                    prior_slice[0] += s["prior_context_needed"] is True
                es, eg = evidence(s), evidence(g)
                ev[0] += len(es & eg)
                ev[1] += len(es)
                ev[2] += len(es & eg)
                ev[3] += len(eg)
                if t == "actions":
                    acc["actions.owner_exact"][1] += 1
                    acc["actions.owner_exact"][0] += set(s["owner"] or []) == set(g["owner"] or [])
                    acc["actions.owner_basis"][1] += 1
                    acc["actions.owner_basis"][0] += s["owner_basis"] == g["owner_basis"]
                    acc["actions.deadline"][1] += 1
                    acc["actions.deadline"][0] += deadline_match(s, g)
                    acc["actions.deadline_basis"][1] += 1
                    acc["actions.deadline_basis"][0] += s["deadline_basis"] == g["deadline_basis"]
                    if not g["deadline"]:
                        invented[1] += 1
                        invented[0] += bool(s["deadline"])
            if verbose:
                print(f"\n== {m} {t}: matched {len(pairs)} / sys {len(ss)} / gold {len(gg)}")
                for s_id, g_id in pairs.items():
                    print(f"  OK  sys: {sid[s_id]['text'][:90]}\n      gold: {gid[g_id]['text'][:90]}")
                for i in ss:
                    if i["id"] not in pairs:
                        print(f"  EXTRA sys: {i['text'][:110]}")
                for i in gg:
                    if i["id"] not in pairs.values():
                        print(f"  MISSED gold: {i['text'][:110]}")
        per_meeting[m] = mc

    # cross-meeting relations over matched items (system target mapped to gold id via the alignment)
    for m in gold_by_m:
        if sys_by_m[m] is None:
            continue
        gold_idx = {i["id"]: i for i in gold_by_m[m]}
        for s in sys_by_m[m]:
            if s["id"] not in amap or s["type"] == "abstract":
                continue
            g = gold_idx[amap[s["id"]]]
            s_pairs = {(amap.get(r["id"]), r["relation"]) for r in s.get("related_items", [])}
            g_pairs = {(r["id"], r["relation"]) for r in g.get("related_items", [])}
            rel[0] += len(s_pairs & g_pairs)
            rel[1] += len(s_pairs)
            rel[2] += len(g_pairs)

    def frac(k):
        c, n = acc[k]
        return {"correct": c, "total": n, "rate": round(c / n, 3) if n else None}

    result = {
        "series": series, "mode": mode,
        "items": {t: prf(*cnt[t]) for t in TYPES},
        "owner_exact": frac("actions.owner_exact"), "owner_basis": frac("actions.owner_basis"),
        "deadline_match": frac("actions.deadline"), "deadline_basis": frac("actions.deadline_basis"),
        "invented_deadline": {"count": invented[0], "of_gold_null": invented[1]},
        "status": {t: frac(f"{t}.status") for t in TYPES},
        "prior_context_needed": frac("all.prior_context_needed"),
        "prior_context_needed_recall_on_gold_true": {"correct": prior_slice[0], "total": prior_slice[1]},
        "related_items": prf(*rel),
        "evidence_overlap_proxy": {
            "system_refs_also_in_gold": round(ev[0] / ev[1], 3) if ev[1] else None,
            "gold_refs_covered": round(ev[2] / ev[3], 3) if ev[3] else None},
        "per_meeting": per_meeting,
        "note": "alignment is " + ("LLM-judged" if matcher else "heuristic") + "; evidence overlap is a proxy, not an evidence-support judgement",
    }
    return result


def show(r: dict) -> None:
    print(f"\n{r['series']}  mode={r['mode']}")
    for t, v in r["items"].items():
        print(f"  {t:10s} P={v['precision']:.2f} R={v['recall']:.2f} F1={v['f1']:.2f}  (tp {v['tp']}, sys {v['sys']}, gold {v['gold']})")
    for k in ("owner_exact", "owner_basis", "deadline_match", "deadline_basis", "prior_context_needed"):
        v = r[k]
        print(f"  {k:22s} {v['correct']}/{v['total']}  rate={v['rate']}")
    for t, v in r["status"].items():
        print(f"  status.{t:11s} {v['correct']}/{v['total']}  rate={v['rate']}")
    inv = r["invented_deadline"]
    print(f"  invented deadline      {inv['count']}/{inv['of_gold_null']} (gold had none)")
    p = r["prior_context_needed_recall_on_gold_true"]
    print(f"  prior_context recall   {p['correct']}/{p['total']} (gold True)")
    v = r["related_items"]
    print(f"  related_items          P={v['precision']:.2f} R={v['recall']:.2f} F1={v['f1']:.2f}  (tp {v['tp']}, sys {v['sys']}, gold {v['gold']})")
    print(f"  evidence overlap proxy {r['evidence_overlap_proxy']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("--run", type=Path, default=config.RUNS / "dev")
    ap.add_argument("--mode", default="s2")
    ap.add_argument("--gold", type=Path, default=GOLD_DIR)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--judge", action="store_true", help="align with the LLM judge (mta.judge.get_judge) instead of the heuristic")
    a = ap.parse_args(argv)
    matcher = None
    if a.judge:
        from .judge import get_judge
        matcher = make_llm_matcher(get_judge())
    r = evaluate(a.series, a.run, a.mode, a.gold, a.verbose, matcher)
    show(r)
    out = a.run / a.mode / f"eval_{a.series}.json"
    out.write_text(json.dumps(r, indent=1), encoding="utf-8")
    print(f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
