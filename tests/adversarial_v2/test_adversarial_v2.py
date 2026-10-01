import pytest

from tests.adversarial_v2.cases import build_scenarios, expectation_ok, invariant_violations, run

SCEN = build_scenarios()
_cache: dict = {}


def outcome(sc):
    if sc.name not in _cache:
        _cache[sc.name] = run(sc)
    return _cache[sc.name]


def test_scenario_count_and_names_unique():
    assert len(SCEN) >= 35
    assert len({s.name for s in SCEN}) == len(SCEN)


@pytest.mark.parametrize("sc", SCEN, ids=lambda s: s.name)
def test_expectation(sc):
    o = outcome(sc)
    assert o.error is None, f"{sc.name}: crash {o.error}\n{sc.doc}"
    assert expectation_ok(o), f"{sc.name}: expected {sc.expect}, got {o.status}\n{sc.doc}"


@pytest.mark.parametrize("sc", SCEN, ids=lambda s: s.name)
def test_invariants(sc):
    v = invariant_violations(outcome(sc))
    assert not v, f"{sc.name}: {v}\n{sc.doc}"


@pytest.mark.parametrize("sc", [s for s in SCEN if s.twin], ids=lambda s: s.name)
def test_injection_does_not_change_status(sc):
    a, b = outcome(sc), run(sc.twin)
    assert a.error is None and b.error is None, (a.error, b.error)
    assert a.status == b.status, f"{sc.name}: injected={a.status} twin={b.status}"
