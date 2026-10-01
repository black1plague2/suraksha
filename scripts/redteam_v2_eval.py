"""Run red-team v2 scenarios; write logs/eval/redteam_v2.json."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from tests.adversarial_v2.cases import build_scenarios, expectation_ok, invariant_violations, run  # noqa: E402


def main() -> int:
    rows = []
    for sc in build_scenarios():
        o = run(sc)
        twin_changed = None
        if sc.twin:
            twin_changed = o.status != run(sc.twin).status
        rows.append(dict(name=sc.name, category=sc.category, expect=sc.expect, status=o.status, error=o.error,
                         expectation_ok=expectation_ok(o), violations=invariant_violations(o),
                         twin_status_changed=twin_changed))
    mf = [r for r in rows if r["expect"] == "must_flag"]
    mc = [r for r in rows if r["expect"] == "must_clear"]
    recall = sum(r["expectation_ok"] for r in mf) / len(mf)
    fpr = sum(not r["expectation_ok"] for r in mc) / len(mc)
    out = dict(n=len(rows), must_flag=len(mf), must_clear=len(mc), recall=recall, fpr=fpr,
               crashes=[r["name"] for r in rows if r["error"]],
               failures=[r for r in rows if not r["expectation_ok"] or r["violations"] or r["twin_status_changed"]],
               rows=rows)
    p = ROOT / "logs" / "eval" / "redteam_v2.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"n={len(rows)} recall={recall:.2%} ({len(mf)}) fpr={fpr:.2%} ({len(mc)}) crashes={len(out['crashes'])}")
    for r in out["failures"]:
        print("FAIL", r["name"], r["expect"], r["status"], r["error"] or r["violations"] or "twin changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
