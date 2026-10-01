"""Evaluate Suraksha on the synthetic set against PRD goals G1–G4.

Usage: python scripts/eval.py [--seed 42] [--json logs/eval/latest.json]
Exit code 1 if any goal fails.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from suraksha.agents.report import validate_citations  # noqa: E402
from suraksha.models import Decision, PipelineStatus  # noqa: E402
from suraksha.pipeline import Suraksha  # noqa: E402
from suraksha.store.memory import MemoryStore  # noqa: E402
from suraksha.synth import generate, load_into  # noqa: E402


def run(seed: int = 42) -> dict:
    ds = generate(seed=seed)
    store = MemoryStore()
    load_into(store, ds)
    app = Suraksha(store)

    results = [app.process(r) for r in ds.requests]
    by_scn: dict[str, Counter] = defaultdict(Counter)
    tp = fn = fp = tn = 0
    for res in results:
        flagged = res.status != PipelineStatus.CLEAR
        label = ds.labels[res.request_id]
        by_scn[ds.scenarios[res.request_id]][res.status.value] += 1
        if label and flagged:
            tp += 1
        elif label:
            fn += 1
        elif flagged:
            fp += 1
        else:
            tn += 1

    citation_errors = {r.report.report_id: validate_citations(r.report) for r in results if r.report}
    citation_errors = {k: v for k, v in citation_errors.items() if v}
    lat = [r.elapsed_ms for r in results]

    # G4: approve one pending case as a named officer; system actors must be refused
    pending = [r.case for r in results if r.case]
    g4 = True
    if pending:
        try:
            app.decide(pending[0].case_id, Decision.APPROVE, "system:auto", "x")
            g4 = False
        except ValueError:
            pass
        c = app.decide(pending[0].case_id, Decision.APPROVE, "officer:Priya Nair", "Confirmed duplicate pledge")
        g4 = g4 and c.status.value == "FILED" and c.hold_recommended
    g4 = g4 and app.audit.verify()

    tpr = tp / (tp + fn) if tp + fn else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    out = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "seed": seed,
        "requests": len(results),
        "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "detection_rate": round(tpr, 4),
        "false_positive_rate": round(fpr, 4),
        "latency_ms": {"p50": round(statistics.median(lat), 2), "max": round(max(lat), 2)},
        "str_drafted": sum(1 for r in results if r.report),
        "citation_errors": citation_errors,
        "by_scenario": {k: dict(v) for k, v in sorted(by_scn.items())},
        "goals": {
            "G1_detection>=0.90": tpr >= 0.90,
            "G1_fpr<0.10": fpr < 0.10,
            "G2_latency<5min": max(lat) < 300_000,
            "G3_all_citations_valid": not citation_errors,
            "G4_human_in_control+audit_chain": g4,
        },
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json", default=str(ROOT / "logs" / "eval" / "latest.json"))
    a = ap.parse_args()
    out = run(a.seed)
    Path(a.json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.json).write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"\nSuraksha eval  seed={out['seed']}  requests={out['requests']}")
    print(f"  detection rate   {out['detection_rate']:.1%}   confusion {out['confusion']}")
    print(f"  false-pos rate   {out['false_positive_rate']:.1%}")
    print(f"  latency p50/max  {out['latency_ms']['p50']} / {out['latency_ms']['max']} ms")
    print(f"  STRs drafted     {out['str_drafted']}   citation errors: {len(out['citation_errors'])}")
    print("  by scenario:")
    for k, v in out["by_scenario"].items():
        print(f"    {k:<38} {v}")
    print("  goals:")
    for k, v in out["goals"].items():
        print(f"    {'PASS' if v else 'FAIL'}  {k}")
    return 0 if all(out["goals"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
