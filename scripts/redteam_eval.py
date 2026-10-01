"""Run the red-team scenarios and report recall / FPR. Writes logs/eval/redteam.json."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from suraksha.models import PipelineStatus  # noqa: E402
from suraksha.pipeline import Suraksha  # noqa: E402
from tests.adversarial.cases import SCENARIOS, build_store  # noqa: E402


def main() -> int:
    rows = []
    for scn in SCENARIOS:
        try:
            store = build_store()
            pipe = Suraksha(store)
            res = [pipe.process(r) for r in scn.requests(store)][-1]
            got = res.status.value
        except Exception as e:  # a crash is a result, not a script failure
            traceback.print_exc()
            got = f"ERROR:{type(e).__name__}"
        flagged = got not in (PipelineStatus.CLEAR.value,) and not got.startswith("ERROR")
        if scn.expect == "must_flag":
            ok = flagged
        elif scn.expect == "must_clear":
            ok = got == PipelineStatus.CLEAR.value
        else:
            ok = None
        rows.append(dict(scenario=scn.name, expected=scn.expect, got=got, passed=ok, doc=scn.doc))

    print(f"{'scenario':42} {'expected':11} {'got':22} result")
    for r in rows:
        res = "n/a" if r["passed"] is None else ("PASS" if r["passed"] else "FAIL")
        print(f"{r['scenario']:42} {r['expected']:11} {r['got']:22} {res}")
    mf = [r for r in rows if r["expected"] == "must_flag"]
    mc = [r for r in rows if r["expected"] == "must_clear"]
    recall = sum(bool(r["passed"]) for r in mf) / max(len(mf), 1)
    fpr = sum(not r["passed"] for r in mc) / max(len(mc), 1)
    print(f"\nrecall on must_flag: {recall:.1%} ({sum(bool(r['passed']) for r in mf)}/{len(mf)})")
    print(f"FPR on must_clear:   {fpr:.1%} ({sum(not r['passed'] for r in mc)}/{len(mc)})")
    out = ROOT / "logs" / "eval" / "redteam.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(recall=recall, fpr=fpr, n_must_flag=len(mf), n_must_clear=len(mc),
                                   scenarios=rows), indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
