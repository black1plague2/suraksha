"""End-to-end: synthetic consortium -> pipeline -> STR -> human approval -> audit chain."""
from __future__ import annotations

import pytest

from suraksha.agents.report import render_markdown, validate_citations
from suraksha.models import CaseStatus, Decision, PipelineStatus
from suraksha.pipeline import Suraksha
from suraksha.store.memory import MemoryStore
from suraksha.synth import generate, load_into

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def run():
    ds = generate(seed=42)
    store = MemoryStore()
    load_into(store, ds)
    app = Suraksha(store)
    results = {r.request_id: app.process(r) for r in ds.requests}
    return ds, store, app, results


def _rate(ds, results, label: bool) -> float:
    ids = [rid for rid, lab in ds.labels.items() if lab is label]
    flagged = sum(results[i].status != PipelineStatus.CLEAR for i in ids)
    return flagged / len(ids)


def test_g1_detection_rate(run):
    ds, _, _, results = run
    assert _rate(ds, results, True) >= 0.90


def test_g1_false_positive_rate(run):
    ds, _, _, results = run
    assert _rate(ds, results, False) < 0.10


def test_g2_latency_under_five_minutes(run):
    _, _, _, results = run
    assert max(r.elapsed_ms for r in results.values()) < 300_000


def test_g3_every_str_fully_cited(run):
    _, _, _, results = run
    drafts = [r.report for r in results.values() if r.report]
    assert drafts, "expected at least one STR"
    for d in drafts:
        assert validate_citations(d) == []
        assert "[^1]" in render_markdown(d)


def test_weak_unlinked_asks_for_evidence(run):
    ds, _, _, results = run
    weak = [rid for rid, s in ds.scenarios.items() if s == "dup_weak_unlinked"]
    assert weak
    for rid in weak:
        assert results[rid].status == PipelineStatus.NEED_MORE_EVIDENCE
        assert results[rid].confidence.missing


def test_originals_and_clean_are_clear(run):
    ds, _, _, results = run
    for rid, s in ds.scenarios.items():
        if s.startswith("dup_original") or s == "clean":
            assert results[rid].status == PipelineStatus.CLEAR, (rid, s)


def test_g4_human_approval_and_audit(run):
    _, store, app, results = run
    case = next(r.case for r in results.values() if r.case)
    assert case.status == CaseStatus.PENDING_APPROVAL and not case.hold_recommended
    with pytest.raises(ValueError):
        app.decide(case.case_id, Decision.APPROVE, "system:bot", "auto")
    filed = app.decide(case.case_id, Decision.APPROVE, "officer:Priya Nair", "Confirmed")
    assert filed.status == CaseStatus.FILED and filed.hold_recommended
    with pytest.raises(ValueError):
        app.decide(case.case_id, Decision.REJECT, "officer:Someone", "late")
    actions = [a.action for a in store.list_audit(case.case_id)]
    assert "CASE_OPENED" in actions
    assert app.audit.verify()


def test_reject_path_closes_case(run):
    _, _, app, results = run
    pending = [r.case for r in results.values() if r.case and r.case.status == CaseStatus.PENDING_APPROVAL]
    case = app.store.get_case(pending[-1].case_id)
    if case.status != CaseStatus.PENDING_APPROVAL:
        pytest.skip("already decided")
    closed = app.decide(case.case_id, Decision.REJECT, "officer:Arjun Mehta", "Legitimate split shipment")
    assert closed.status == CaseStatus.CLOSED and not closed.hold_recommended


def test_consortium_holds_no_names_or_amounts(run):
    _, store, _, _ = run
    names = {c["name"] for c in store.list_companies()}
    for e in store.list_consortium():
        blob = repr(e)
        assert not any(n in blob for n in names)
        assert all(len(v) == 64 for v in e.keys.values())
