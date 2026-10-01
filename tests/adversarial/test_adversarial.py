"""Scored adversarial tests. Misses fail honestly (no xfail)."""
from __future__ import annotations

import pytest

from suraksha.models import PipelineStatus
from suraksha.pipeline import Suraksha

from .cases import SCENARIOS, build_store


def run_scenario(scn):
    store = build_store()
    pipe = Suraksha(store)
    results = [pipe.process(r) for r in scn.requests(store)]
    return results[-1]


@pytest.mark.parametrize("scn", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario(scn, capsys):
    res = run_scenario(scn)
    if scn.expect == "either":
        print(f"[either] {scn.name}: {res.status.value}")
        return
    if scn.expect == "must_flag":
        assert res.status != PipelineStatus.CLEAR, f"MISS: {scn.name} returned CLEAR. {scn.doc}"
    else:
        assert res.status == PipelineStatus.CLEAR, f"FALSE POSITIVE: {scn.name} returned {res.status.value}. {scn.doc}"
