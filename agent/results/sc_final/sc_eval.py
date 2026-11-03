"""Score the frozen SC runs (ES2004, TS3007; b0/s1/s2) with the project's own scorers, unchanged.

The SC guard is lifted only inside this process (config.SC_SERIES emptied) after the runs exist; no project source is
edited, so the recorded source digest stays valid. Efficiency comes from <meeting>.run.json (no _timing logs here).
Usage (from agent/, PYTHONPATH=src): python sc_eval.py --run runs/final --gold ../revised_gold_output
"""
import argparse
import importlib.util
import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mta import config
from mta import evaluate as ev
from mta.judge import get_judge

config.SC_SERIES = set()  # lift the held-out guard for scoring only

SERIES = ["ES2004", "TS3007"]
MODES = ["b0", "s1", "s2"]


def load_full_eval():
    spec = importlib.util.spec_from_file_location("full_eval", config.ROOT / "scripts" / "full_eval.py")
    fe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fe)
    fe.SERIES = SERIES
    fe.MEETINGS = [f"{s}{c}" for s in SERIES for c in "abcd"]
    return fe


def efficiency(run: Path, meetings: list[str]) -> dict:
    out = {}
    for mode in MODES:
        rows = []
        for m in meetings:
            f = run / mode / f"{m}.run.json"
            if f.exists():
                d = json.loads(f.read_text(encoding="utf-8"))
                rows.append({"meeting": m, "status": d["status"], "sec": d.get("elapsed_seconds"),
                             "calls": d["usage"]["calls"], "in": d["usage"]["in"], "out": d["usage"]["out"],
                             "stages": d["usage"].get("stages", {}), "digest": d["code"]["source_digest"],
                             "model": d["model"].get("name"), "profile": (d.get("quality_policy") or {}).get("name")})
        if not rows:
            continue
        lat = [r["sec"] for r in rows if r["sec"] is not None]
        tot = [r["in"] + r["out"] for r in rows]
        out[mode] = {"meetings": len(rows), "per_meeting": rows,
                     "latency_sec": {"mean": round(statistics.mean(lat), 1), "max": round(max(lat), 1)},
                     "tokens": {"input_mean": round(statistics.mean(r["in"] for r in rows)),
                                "output_mean": round(statistics.mean(r["out"] for r in rows)),
                                "total_mean": round(statistics.mean(tot)), "total_max": max(tot)},
                     "calls": {"mean": round(statistics.mean(r["calls"] for r in rows), 1),
                               "max": max(r["calls"] for r in rows)}}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--gold", type=Path, required=True)
    a = ap.parse_args()
    fe = load_full_eval()
    llm = get_judge()
    matcher = ev.make_llm_matcher(llm)
    with ThreadPoolExecutor(max_workers=3) as pool:
        ext = dict(zip(MODES, pool.map(lambda m: fe.extraction(a.run, m, a.gold, llm), MODES)))
        tol = dict(zip(MODES, pool.map(lambda m: fe.tolerant_extraction(a.run, m, a.gold, llm), MODES)))
        sup = dict(zip(MODES, pool.map(lambda m: fe.support_rate(a.run, m, llm), MODES)))
    tol_series = {s: {m: fe.tolerant_extraction(a.run, m, a.gold, llm, [f"{s}{c}" for c in "abcd"]) for m in MODES}
                  for s in SERIES}
    detail = {s: {m: ev.evaluate(s, a.run, m, a.gold, matcher=matcher) for m in MODES} for s in SERIES}
    res = {"extraction": ext, "tolerant": tol, "tolerant_by_series": tol_series, "support": sup, "detail": detail,
           "summary": fe.summary_quality(a.run, a.gold, llm),
           "efficiency": efficiency(a.run, fe.MEETINGS),
           "judge": {"model": llm.model, "effort": getattr(llm, "effort", None) or "model default",
                     "votes": config.EVAL_VOTES, "calls": getattr(llm, "calls", None),
                     "input_tokens": llm.in_tokens, "output_tokens": llm.out_tokens}}
    (a.run / "sc_eval.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("tolerant", "judge")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
