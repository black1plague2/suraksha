from datetime import datetime

from suraksha.agents.confidence import score
from suraksha.config import RULE_WEIGHTS, Settings
from suraksha.models import (
    Citation,
    CitationKind,
    ConfidenceBand,
    ConsortiumEntry,
    ConsortiumMatch,
    GraphEdge,
    Investigation,
    MatchType,
    OwnershipPath,
)

S = Settings()


def inv(match_type=MatchType.EXACT, **kw):
    e = ConsortiumEntry("E1", "BANK_A", {}, 1, "t", datetime(2026, 6, 1))
    m = ConsortiumMatch("R1", e, match_type, ["exact"], 1.0, Citation(CitationKind.CONSORTIUM, "E1"))
    base = dict(request_id="R1", borrower_id="C1", counterparty_company_id="C2", match=m, paths=[],
                shared_directors=[], shared_ubos=[], shared_addresses=[], shared_phones=[], transactions=[],
                policy_clauses=[], timing_overlap_days=None)
    base.update(kw)
    return Investigation(**base)


def rules(r):
    return [e.rule_id for e in r.evidence]


def test_exact_alone_is_weight_only():
    r = score(inv(), S)
    assert r.score == RULE_WEIGHTS["R_EXACT_HASH"] and r.band == ConfidenceBand.LOW and r.missing


def test_sum_capped_at_one():
    r = score(inv(shared_ubos=["P1"], shared_directors=["P2"], shared_addresses=["A1"], shared_phones=["1"],
                  timing_overlap_days=1), S)
    assert r.score == 1.0 and r.band == ConfidenceBand.HIGH and r.missing == []


def test_timing_window_boundary():
    assert "R_TIMING_OVERLAP" in rules(score(inv(timing_overlap_days=S.timing_window_days), S))
    assert "R_TIMING_OVERLAP" not in rules(score(inv(timing_overlap_days=S.timing_window_days + 1), S))


def test_ownership_path_rule_and_citations():
    edge = GraphEdge("company:C1", "company:C2", "OWNS", Citation(CitationKind.REGISTRY, "corp_owners:C1/C2"))
    r = score(inv(paths=[OwnershipPath("C1", "C2", [edge])]), S)
    own = next(e for e in r.evidence if e.rule_id == "R_CORP_OWNERSHIP")
    assert any(c.ref == "corp_owners:C1/C2" for c in own.citations)
    assert r.score == 0.75 and r.band == ConfidenceBand.HIGH


def test_non_owns_path_not_ownership():
    edge = GraphEdge("person:P", "company:C2", "DIRECTOR_OF", Citation(CitationKind.REGISTRY, "roles:C2/P/DIRECTOR"))
    r = score(inv(paths=[OwnershipPath("C1", "C2", [edge])]), S)
    assert "R_CORP_OWNERSHIP" not in rules(r)


def test_fuzzy_rule_and_missing_asks():
    r = score(inv(MatchType.FUZZY, timing_overlap_days=100), S)
    assert rules(r) == ["R_FUZZY_MATCH"]
    assert r.band == ConfidenceBand.LOW
    assert any("Obtain original B/L from carrier" in m for m in r.missing)
    assert any("Request UBO declaration" in m for m in r.missing)


def test_all_evidence_has_citations():
    r = score(inv(shared_ubos=["P1"], shared_phones=["1"], timing_overlap_days=2), S)
    assert r.evidence and all(e.citations and e.citations[0].kind == CitationKind.RULE for e in r.evidence)
