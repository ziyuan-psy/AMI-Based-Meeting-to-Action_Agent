"""Development-only gold-aware diagnostics. Never imported by the extraction runtime."""
from __future__ import annotations

from collections import Counter

from .evaluate import MATCH_SCHEMA, TYPES, make_llm_matcher, prf
from .trace import StageTrace

COVERAGE_SYSTEM = """Locate each GOLD decision in the CANDIDATE list from the same meeting.
Return pairs {sys: candidate index, gold: gold index} only when the candidate actually states the same choice,
including its essential scope. A broad candidate can cover several distinct choices, and several duplicate
candidates can cover one gold choice: this is coverage diagnosis, NOT one-to-one metric alignment.
Merely sharing a topic, component or speaker is not enough. Do not infer an unstated choice."""


def as_records(items):
    return [{**item, "id": str(n), "reference": [{"source_type": "dialogue", "locator": e}
             for e in item["evidence_da_ids"]]} for n, item in enumerate(items)]


def score(items, gold, matcher):
    records = as_records(items)
    metrics, pairs = {}, {}
    for typ in TYPES:
        ss, gg = [i for i in records if i["type"] == typ], [g for g in gold if g["type"] == typ]
        matches = matcher(ss, gg)
        metrics[typ] = prf(len(matches), len(ss), len(gg))
        pairs.update(matches)
    return {"metrics": metrics, "pairs": pairs}


def candidate_coverage(candidates, gold, llm, votes=3):
    user = "CANDIDATES:\n" + "\n".join(f"[{i}] ({c['type']}) {c['text']}" for i, c in enumerate(candidates))
    user += "\n\nGOLD decisions:\n" + "\n".join(f"[{i}] {g['text']}" for i, g in enumerate(gold))
    runs, counts = [], Counter()
    for _ in range(votes):
        pairs = llm.forced_tool(COVERAGE_SYSTEM, user, "submit_matches", MATCH_SCHEMA,
                                stage="decision_diagnosis").get("pairs") or []
        runs.append(pairs)
        counts.update({(p.get("sys"), p.get("gold")) for p in pairs if isinstance(p, dict)
                       and type(p.get("sys")) is int and type(p.get("gold")) is int
                       and 0 <= p["sys"] < len(candidates) and 0 <= p["gold"] < len(gold)})
    return {"votes": votes, "raw_votes": runs,
            "matches": [{"candidate": c, "gold_id": gold[g]["id"], "votes": n}
                        for (c, g), n in sorted(counts.items()) if n >= votes // 2 + 1]}


def hydrate_runs(saved):
    """Recover member identities from recorded merge lineage, including old pre-lineage caches."""
    runs = []
    for index, run in enumerate(saved["runs"]):
        details = saved.get("run_traces", [])[index]
        if not details:
            raise ValueError("Detailed consolidation traces are required for a causal replay")
        merged = next(e for e in details[-1]["events"] if e["event"] == "merged")
        hydrated = []
        for item in run:
            original = next(i for i, x in enumerate(merged["items"]) if x == item)
            hydrated.append({**item, "candidate_indexes": merged["lineage"][original]["candidate_indexes"]})
        runs.append(hydrated)
    return runs


def replay(saved, candidates, module):
    """Replay real raw model groups through an explicit implementation; no model calls."""
    trace, runs = StageTrace(), []
    for sample, attempts in enumerate(saved["run_traces"]):
        groups = next(e["groups"] for e in attempts[-1]["events"] if e["event"] == "raw_groups")
        result = module._merge_groups(groups, candidates, trace)
        trace.log(stage="consolidate", event="sample", sample=sample, items=result)
        runs.append(result)
    voted = module.vote_items(runs, trace)
    final = module.restore_actions(voted, candidates, trace)
    return {"runs": runs, "voted": voted, "final": final, "events": trace.events}


def explain(gold, coverage, candidates, arm, alignment):
    """Follow all candidate witnesses for an unmatched gold choice; expose evidence for each attribution."""
    reports = []
    for gold_item in gold:
        witnesses = {m["candidate"] for m in coverage["matches"] if m["gold_id"] == gold_item["id"]}
        def contains(item):
            return bool(witnesses & set(item.get("candidate_indexes", [])))
        merged, capped = [], []
        for attempts in arm["cache"]["run_traces"]:
            events = attempts[-1]["events"]
            merged.append([i for e in events if e["event"] == "merged" for i in e["items"] if contains(i)])
            capped.append([i for e in events if e["event"] == "cap" for i in e["kept"] if contains(i)])
        vote_kept = [i for i in arm["voted"] if contains(i)]
        final_kept = [i for i in arm["normalized"] if contains(i)]
        matched = gold_item["id"] in alignment["pairs"].values()
        if matched:
            reason = "recovered"
        elif not witnesses:
            reason = "initial_extraction_or_type"
        elif not any(merged):
            reason = "merge_selection"
        elif sum(bool(x) for x in capped) < sum(bool(x) for x in merged) and not vote_kept:
            reason = "sample_cap_then_vote"
        elif not vote_kept:
            reason = "majority_vote"
        elif not final_kept:
            reason = "final_cap_or_normalization"
        else:
            reason = "merge_granularity_or_wording"
        reports.append({"gold_id": gold_item["id"], "gold_text": gold_item["text"], "matched": matched,
                        "attribution": reason, "candidate_witnesses": sorted(witnesses),
                        "candidate_items": [candidates[i] for i in sorted(witnesses)],
                        "merged_samples": merged, "after_sample_caps": capped,
                        "after_vote": vote_kept, "after_final_cap_and_normalize": final_kept})
    return reports
