"""Full evaluation of one run folder over the development series.

Metrics
- Extraction P/R/F1 per type (actions, decisions, problems = "issues"), one-to-one LLM alignment with gold
  (same matcher and cache as `mta.evaluate --judge`). Gold items used as few-shot examples in the prompts are
  excluded, together with the system items aligned to them (docs/FEWSHOT_EXAMPLES.md).
- Owner accuracy: matched actions whose owner set equals the gold owner set.
- Support rate: share of items whose cited dialogue supports the statement (LLM judge from `mta.support`, which sees
  only the claim and the cited lines). Also the structural rate: items with at least one dialogue reference.
- Summary quality (1-5, LLM judge): faithfulness of the abstract sentences to the transcript; coverage of the gold
  abstract sentences. The judge never sees which system wrote the summary. Gold abstracts are also scored for
  faithfulness as a calibration point for the judge.
- Efficiency per meeting: latency (mean, p95), tokens, LLM calls. Latency and tokens come from the bench logs in
  <run>/_timing; LLM calls are counted from the pipeline structure and the agent trace (a lower bound: retries inside
  forced_tool and empty-result retries are not logged).

Usage (from agent/, PYTHONPATH=src):  python scripts/full_eval.py --run runs/new
Writes <run>/full_eval.json and <run>/EVALUATION.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mta import config, support
from mta.evaluate import TYPES, make_llm_matcher
from mta.extract import chunks
from mta.judge import get_judge, shared_cache
from mta.store import Store
from mta.transcript_parser import parse_transcript_file

SERIES = ["ES2011", "IS1008", "TS3004", "TS3006"]
MODES = ["b0", "s1", "s2"]
MEETINGS = [f"{s}{c}" for s in SERIES for c in "abcd"]
FEWSHOT_GOLD = {"IS1008b.JacquelinePalmer.s.10", "IS1008b.JacquelinePalmer.s.12", "ES2011b.revised.s.23",
                "ES2011b.JacquelinePalmer.s.13", "ES2011b.JacquelinePalmer.s.14", "ES2011b.JacquelinePalmer.s.15",
                "TS3004c.JacquelinePalmer.s.19", "TS3004c.JacquelinePalmer.s.17", "ES2011b.JacquelinePalmer.s.20",
                "IS1008b.JacquelinePalmer.s.17"}
NL = chr(10)


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def prf(tp: int, sys_n: int, gold_n: int) -> dict:
    p = tp / sys_n if sys_n else 0.0
    r = tp / gold_n if gold_n else 0.0
    return {"tp": tp, "sys": sys_n, "gold": gold_n, "precision": round(p, 3), "recall": round(r, 3),
            "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


# ---- extraction and owner -------------------------------------------------------------------------------------
def extraction(run: Path, mode: str, gold_dir: Path, llm) -> dict:
    matcher = make_llm_matcher(llm)  # shared, voted alignment (config.EVAL_VOTES runs per item list)
    cnt = {t: [0, 0, 0] for t in TYPES}
    owner = [0, 0]
    excluded = 0
    for m in MEETINGS:
        sys_items, gold_items = load(run / mode / f"{m}.json"), load(gold_dir / f"{m}.json")
        for t in TYPES:
            ss = [i for i in sys_items if i["type"] == t]
            gg = [i for i in gold_items if i["type"] == t]
            pairs = matcher(ss, gg)  # aligned against the full gold, as in mta.evaluate
            dropped_sys = {s for s, g in pairs.items() if g in FEWSHOT_GOLD}
            gold_kept = [g for g in gg if g["id"] not in FEWSHOT_GOLD]
            excluded += len(gg) - len(gold_kept)
            kept = {s: g for s, g in pairs.items() if s not in dropped_sys}
            cnt[t][0] += len(kept)
            cnt[t][1] += len(ss) - len(dropped_sys)
            cnt[t][2] += len(gold_kept)
            if t == "actions":
                sid, gid = {i["id"]: i for i in ss}, {i["id"]: i for i in gg}
                for s, g in kept.items():
                    owner[1] += 1
                    owner[0] += set(sid[s]["owner"] or []) == set(gid[g]["owner"] or [])
    return {"per_type": {t: prf(*cnt[t]) for t in TYPES},
            "owner_accuracy": {"correct": owner[0], "total": owner[1],
                               "rate": round(owner[0] / owner[1], 3) if owner[1] else None},
            "fewshot_gold_items_excluded": excluded}


def tolerant_extraction(run: Path, mode: str, gold_dir: Path, llm, meetings: list[str] | None = None) -> dict:
    """Split/merge-tolerant P/R/F1 (mta.evaluate.make_tolerant_matcher + tolerant_prf), reported next to the strict
    one-to-one scores. Few-shot gold items are removed, with the system items linked only to them. Owner accuracy is
    over linked action pairs (each kept link is one pair)."""
    from mta.evaluate import make_tolerant_matcher, tolerant_prf
    matcher = make_tolerant_matcher(llm)
    acc = {t: {"linked_sys": 0, "sys": 0, "covered_gold": 0, "gold": 0, "relations": {"same": 0, "split": 0, "merge": 0},
               "duplicate_links_removed": 0, "duplicates_counted_fp": 0} for t in TYPES}
    owner = [0, 0]
    for m in meetings or MEETINGS:
        sys_items, gold_items = load(run / mode / f"{m}.json"), load(gold_dir / f"{m}.json")
        for t in TYPES:
            ss = [i for i in sys_items if i["type"] == t]
            gg = [i for i in gold_items if i["type"] == t]
            links = matcher(ss, gg)  # judged against the full gold, as in the strict score
            only_fewshot = {s["id"] for s in ss if (gs := {g for x, g, _ in links if x == s["id"]}) and gs <= FEWSHOT_GOLD}
            sys_ids = [s["id"] for s in ss if s["id"] not in only_fewshot]
            gold_ids = [g["id"] for g in gg if g["id"] not in FEWSHOT_GOLD]
            kept = [x for x in links if x[0] not in only_fewshot and x[1] not in FEWSHOT_GOLD]
            r = tolerant_prf(sys_ids, gold_ids, kept)
            a = acc[t]
            for k in ("linked_sys", "sys", "covered_gold", "gold", "duplicate_links_removed", "duplicates_counted_fp"):
                a[k] += r[k]
            for k, v in r["relations"].items():
                a["relations"][k] += v
            if t == "actions":
                sid, gid = {i["id"]: i for i in ss}, {i["id"]: i for i in gg}
                for s, g, _ in r["kept_links"]:
                    owner[1] += 1
                    owner[0] += set(sid[s]["owner"] or []) == set(gid[g]["owner"] or [])
    for a in acc.values():
        p = a["linked_sys"] / a["sys"] if a["sys"] else 0.0
        rr = a["covered_gold"] / a["gold"] if a["gold"] else 0.0
        a.update(precision=round(p, 3), recall=round(rr, 3), f1=round(2 * p * rr / (p + rr), 3) if p + rr else 0.0)
    return {"per_type": acc, "owner_accuracy": {"correct": owner[0], "total": owner[1],
                                                "rate": round(owner[0] / owner[1], 3) if owner[1] else None}}


# ---- support ----------------------------------------------------------------------------------------------------
def support_rate(run: Path, mode: str, llm) -> dict:
    stmt = {"supported": 0, "partial": 0, "unsupported": 0}
    per_type = {}
    for s in SERIES:
        r = support.run(s, run, mode, llm, sample_n=30)
        for k, v in r["support"].items():
            if k.endswith(".statement"):
                t = k.split(".")[0]
                acc = per_type.setdefault(t, {"supported": 0, "partial": 0, "unsupported": 0})
                for lv in stmt:
                    stmt[lv] += v[lv]
                    acc[lv] += v[lv]
    n_items = n_with_refs = 0
    for m in MEETINGS:
        for it in load(run / mode / f"{m}.json"):
            if it["type"] != "abstract":
                n_items += 1
                n_with_refs += any(r["source_type"] == "dialogue" for r in it["reference"])
    judged = sum(stmt.values())

    def rate(c):
        n = sum(c.values())
        return {**c, "n": n, "supported_rate": round(c["supported"] / n, 3) if n else None,
                "supported_or_partial_rate": round((c["supported"] + c["partial"]) / n, 3) if n else None}

    return {"structural_rate": round(n_with_refs / n_items, 3) if n_items else None, "items": n_items,
            "judged": judged, "statement": rate(stmt), "per_type": {t: rate(c) for t, c in sorted(per_type.items())}}


# ---- summary quality --------------------------------------------------------------------------------------------
FAITH_SYSTEM = """You check a meeting summary against the meeting transcript. For EACH summary sentence decide whether
the transcript supports it: supported (stated or clearly implied), partial (partly supported, or overstated / imprecise),
unsupported (not in the transcript or contradicted). Then give one faithfulness score:
5 = every sentence supported; 4 = all supported but one is slightly imprecise; 3 = one sentence partial or unsupported;
2 = several sentences partial or unsupported; 1 = most sentences unsupported.
Judge only from the transcript. Speakers are shown by role."""
FAITH_SCHEMA = {"type": "object", "properties": {
    "sentences": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"}, "verdict": {"enum": ["supported", "partial", "unsupported"]}},
        "required": ["index", "verdict"]}},
    "score": {"type": "integer", "minimum": 1, "maximum": 5}, "reason": {"type": "string"}},
    "required": ["sentences", "score"]}

COVER_SYSTEM = """You compare a meeting summary with REFERENCE points written by an annotator for the same meeting.
For EACH reference point decide whether the summary conveys it: covered, partial, or missed (paraphrase is fine).
Then give one coverage score:
5 = all or nearly all points covered; 4 = most covered, one or two missed; 3 = about half covered;
2 = only a few covered; 1 = almost none covered.
Do not reward content that is not among the reference points."""
COVER_SCHEMA = {"type": "object", "properties": {
    "points": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"}, "verdict": {"enum": ["covered", "partial", "missed"]}},
        "required": ["index", "verdict"]}},
    "score": {"type": "integer", "minimum": 1, "maximum": 5}, "reason": {"type": "string"}},
    "required": ["points", "score"]}

def transcript_text(meeting_id: str) -> str:
    store = Store(meeting_id, allow_prior=False)
    m = store.meeting(meeting_id)
    return NL.join(f"{a['attributes']['role']}: {a['text']}" for a in m.acts)


def judged(llm, cache, system: str, user: str, name: str, schema: dict) -> dict:
    """`cache`: the shared summary-judge cache (thread-safe JsonCache)."""
    key = hashlib.sha256((system + user).encode()).hexdigest()[:24]
    return cache.compute(key, lambda: llm.forced_tool(system, user, name, schema))


def faithfulness(llm, cache, meeting_id: str, sentences: list[str]) -> dict:
    if not sentences:
        return {"score": None, "n": 0}
    user = ("TRANSCRIPT:" + NL + transcript_text(meeting_id) + NL + NL + "SUMMARY SENTENCES:" + NL
            + NL.join(f"[{i}] {s}" for i, s in enumerate(sentences)))
    r = judged(llm, cache, FAITH_SYSTEM, user, "submit_faithfulness", FAITH_SCHEMA)
    verdicts = [v.get("verdict") for v in r.get("sentences", []) if isinstance(v, dict)]
    return {"score": r.get("score"), "n": len(sentences),
            "supported": verdicts.count("supported"), "partial": verdicts.count("partial"),
            "unsupported": verdicts.count("unsupported")}


def coverage(llm, cache, sentences: list[str], reference: list[str]) -> dict:
    if not reference:
        return {"score": None, "n": 0}
    user = ("REFERENCE POINTS:" + NL + NL.join(f"[{i}] {s}" for i, s in enumerate(reference)) + NL + NL
            + "SUMMARY:" + NL + (" ".join(sentences) if sentences else "(empty summary)"))
    r = judged(llm, cache, COVER_SYSTEM, user, "submit_coverage", COVER_SCHEMA)
    verdicts = [v.get("verdict") for v in r.get("points", []) if isinstance(v, dict)]
    return {"score": r.get("score"), "n": len(reference), "covered": verdicts.count("covered"),
            "partial": verdicts.count("partial"), "missed": verdicts.count("missed")}


def summary_quality(run: Path, gold_dir: Path, llm) -> dict:
    cache = shared_cache("summary_judge_cache", llm)
    jobs = []
    for m in MEETINGS:
        gold_abs = [i["text"] for i in load(gold_dir / f"{m}.json") if i["type"] == "abstract"]
        jobs.append(("gold", m, "faith", gold_abs, None))
        for mode in MODES:
            sys_abs = [i["text"] for i in load(run / mode / f"{m}.json") if i["type"] == "abstract"]
            jobs.append((mode, m, "faith", sys_abs, None))
            jobs.append((mode, m, "cover", sys_abs, gold_abs))

    def do(job):
        who, m, kind, sents, ref = job
        if kind == "faith":
            return job, faithfulness(llm, cache, m, sents)
        return job, coverage(llm, cache, sents, ref)

    with ThreadPoolExecutor(max_workers=max(1, config.WORKERS) * 2) as pool:
        results = list(pool.map(do, jobs))
    out: dict = {}
    for (who, m, kind, _, _), r in results:
        out.setdefault(who, {}).setdefault(kind, {})[m] = r

    def agg(per_meeting: dict) -> dict:
        scores = [r["score"] for r in per_meeting.values() if isinstance(r.get("score"), int)]
        return {"mean": round(statistics.mean(scores), 2) if scores else None,
                "min": min(scores) if scores else None, "n_meetings": len(scores),
                "per_meeting": per_meeting}

    return {who: {kind: agg(v) for kind, v in kinds.items()} for who, kinds in out.items()}


# ---- efficiency -------------------------------------------------------------------------------------------------
def p95(values: list[float]) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]


def timing_rows(run: Path) -> dict:
    """(mode, meeting) -> bench row. Later logs override earlier ones (the TS3006 s2 rerun replaces a crashed run)."""
    rows = {}
    files = sorted((run / "_timing").glob("*.log"), key=lambda p: (p.stem.count("_"), p.name))
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("{") and '"meeting"' in line:
                d = json.loads(line)
                rows[(d["mode"], d["meeting"])] = d
    return rows


def llm_calls(run: Path, mode: str, meeting: str) -> dict:
    n_acts = len(parse_transcript_file(config.TRANSCRIPTS / f"{meeting}.txt"))
    trace = run / mode / f"{meeting}.trace.jsonl"
    agent = 0
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            if json.loads(line).get("outcome") not in ("gave_up", "agent_error"):
                agent += 1  # every other trace event is one model turn
    if mode == "b0":
        extract, consolidate = 1, 0
    elif mode == "s1":
        extract, consolidate = len(list(chunks(n_acts, config.CHUNK, config.OVERLAP))) + 1, 1
    else:  # s2 reused the s1 extraction and consolidation caches in this run
        extract, consolidate = 0, 0
    return {"extract": extract, "consolidate": consolidate, "agent": agent, "abstract": 1,
            "total": extract + consolidate + agent + 1}


def efficiency(run: Path) -> dict:
    rows = timing_rows(run)
    out = {}
    for mode in MODES:
        lat, tin, tout, calls, agent = [], [], [], [], []
        for m in MEETINGS:
            d = rows.get((mode, m))
            if not d:
                continue
            lat.append(d["sec"])
            tin.append(d["input tokens"])
            tout.append(d["output tokens"])
            c = llm_calls(run, mode, m)
            calls.append(c["total"])
            agent.append(c["agent"])
        out[mode] = {"meetings": len(lat),
                     "latency_sec": {"mean": round(statistics.mean(lat), 1), "p95": p95(lat), "max": max(lat)},
                     "tokens_per_meeting": {"input_mean": round(statistics.mean(tin)), "output_mean": round(statistics.mean(tout)),
                                            "total_mean": round(statistics.mean([a + b for a, b in zip(tin, tout)])),
                                            "total_p95": p95([a + b for a, b in zip(tin, tout)])},
                     "llm_calls_per_meeting": {"mean": round(statistics.mean(calls), 1), "p95": p95(calls),
                                               "agent_mean": round(statistics.mean(agent), 1)}}
    return out


# ---- report -----------------------------------------------------------------------------------------------------
def render(res: dict, run: Path) -> str:
    L = [f"# Evaluation of `{run.as_posix()}`", "",
         f"Development series ES2011, IS1008, TS3004, TS3006 (16 meetings). Pipeline model: gpt-5-nano via Portkey. "
         f"Judge: {res['judge']['model']} (effort {res['judge']['effort']}) for alignment, evidence support and summary "
         f"quality. Alignment with gold: majority of {config.EVAL_VOTES} judge runs, shared across runs and modes "
         "(identical item lists get identical alignments). Few-shot gold items are excluded from extraction scores.", "",
         "## Extraction (P / R / F1)", "", "| Mode | Actions | Decisions | Issues (problems) | Owner accuracy |", "|---|---|---|---|---|"]
    for mode in MODES:
        e = res["extraction"][mode]
        cell = lambda t: (f"{e['per_type'][t]['precision']:.2f} / {e['per_type'][t]['recall']:.2f} / "
                          f"**{e['per_type'][t]['f1']:.2f}** ({e['per_type'][t]['tp']}/{e['per_type'][t]['sys']}/{e['per_type'][t]['gold']})")
        o = e["owner_accuracy"]
        L.append(f"| {mode} | {cell('actions')} | {cell('decisions')} | {cell('problems')} | {o['correct']}/{o['total']} = {o['rate']} |")
    L += ["", "Counts in brackets: matched / system items / gold items. Owner accuracy is over matched actions.", "",
          "## Extraction, split/merge tolerant (P / R / F1)", "",
          "| Mode | Actions | Decisions | Issues (problems) | Owner accuracy (linked pairs) |", "|---|---|---|---|---|"]
    for mode in MODES:
        e = res["tolerant"][mode]
        cell = lambda t: (f"{e['per_type'][t]['precision']:.2f} / {e['per_type'][t]['recall']:.2f} / "
                          f"**{e['per_type'][t]['f1']:.2f}** ({e['per_type'][t]['linked_sys']}/{e['per_type'][t]['sys']}, "
                          f"{e['per_type'][t]['covered_gold']}/{e['per_type'][t]['gold']})")
        o = e["owner_accuracy"]
        L.append(f"| {mode} | {cell('actions')} | {cell('decisions')} | {cell('problems')} | {o['correct']}/{o['total']} = {o['rate']} |")
    L += ["", "Many-to-many links labelled same / split / merge (majority of the judge runs). Counts: linked system items / "
          "system items, covered gold items / gold items. A gold item restated in full by several system items counts "
          "once; the extra restatements are false positives.", "",
          "## Support rate", "", "| Mode | Items | With a dialogue reference | Statement supported | Supported or partial |", "|---|---|---|---|---|"]
    for mode in MODES:
        s = res["support"][mode]
        L.append(f"| {mode} | {s['items']} | {s['structural_rate']} | {s['statement']['supported_rate']} | {s['statement']['supported_or_partial_rate']} |")
    L += ["", "## Summary quality (1-5, mean over meetings)", "", "| Summaries | Faithfulness | Coverage of gold abstract |", "|---|---|---|"]
    for who in MODES + ["gold"]:
        q = res["summary"][who]
        cov = q.get("cover", {}).get("mean", "-")
        L.append(f"| {who} | {q['faith']['mean']} (min {q['faith']['min']}) | {cov if cov is not None else '-'} |")
    L += ["", "## Efficiency (per meeting)", "", "| Mode | Latency mean / p95 (s) | Tokens mean (in + out) / p95 | LLM calls mean / p95 (agent) |", "|---|---|---|---|"]
    for mode in MODES:
        f = res["efficiency"][mode]
        t = f["tokens_per_meeting"]
        c = f["llm_calls_per_meeting"]
        L.append(f"| {mode} | {f['latency_sec']['mean']} / {f['latency_sec']['p95']} | {t['total_mean']:,} ({t['input_mean']:,} + {t['output_mean']:,}) / {t['total_p95']:,} | {c['mean']} / {c['p95']} ({c['agent_mean']}) |")
    L += ["", "s2 reused the s1 extraction and consolidation caches, so its latency, tokens and calls cover only the agent "
          "and summary steps. LLM calls are a lower bound (forced-tool and empty-result retries are not logged)."]
    return NL.join(L) + NL


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=config.RUNS / "new")
    ap.add_argument("--gold", type=Path, default=config.ROOT.parent / "revised_gold_output")
    a = ap.parse_args()
    llm = get_judge()
    with ThreadPoolExecutor(max_workers=3) as pool:  # one thread per mode: each mode has its own cache files
        ext = dict(zip(MODES, pool.map(lambda m: extraction(a.run, m, a.gold, llm), MODES)))
        tol = dict(zip(MODES, pool.map(lambda m: tolerant_extraction(a.run, m, a.gold, llm), MODES)))
        sup = dict(zip(MODES, pool.map(lambda m: support_rate(a.run, m, llm), MODES)))
    res = {"extraction": ext, "tolerant": tol, "support": sup, "summary": summary_quality(a.run, a.gold, llm),
           "efficiency": efficiency(a.run),
           "judge": {"model": llm.model, "effort": getattr(llm, "effort", None) or "model default",
                     "calls": getattr(llm, "calls", None), "input_tokens": llm.in_tokens, "output_tokens": llm.out_tokens}}
    (a.run / "full_eval.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (a.run / "EVALUATION.md").write_text(render(res, a.run), encoding="utf-8")
    print(render(res, a.run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
