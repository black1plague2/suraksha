import hashlib
from datetime import datetime, timedelta

from suraksha.agents.confidence import score
from suraksha.agents.investigator import investigate, resolve_borrower
from suraksha.config import Settings
from suraksha.models import (
    Citation,
    CitationKind,
    ConfidenceBand,
    ConsortiumEntry,
    ConsortiumMatch,
    FinancingRequest,
    MatchType,
)
from suraksha.store.memory import MemoryStore

SALT = "test-salt"
S = Settings(consortium_salt=SALT)
T0 = datetime(2026, 6, 1)


def tok(reg):
    return hashlib.sha256((SALT + "|" + reg).encode()).hexdigest()


def comp(cid, name, addr="A9", phone=None):
    return {"company_id": cid, "name": name, "reg_no": f"REG-{cid}", "address_id": addr,
            "phone": phone or f"+91-{cid}", "incorporated": None, "country": "IN"}


def make_store():
    st = MemoryStore()
    st.load_registry(
        companies=[comp("C0001", "Alpha Steel Pvt Ltd", "A1"), comp("C0002", "Zeta Traders Pvt Ltd", "A2"),
                   comp("C0003", "Holdco Pvt Ltd", "A3"), comp("C0004", "Unrelated Ltd", "A4"),
                   comp("C0005", "Director Sharer Ltd", "A5"), comp("C0006", "Same Phone Ltd", "A6", "+91-C0001")],
        persons=[{"person_id": f"P{i}", "name": f"Person {i}", "id_hash": "x"} for i in range(1, 5)],
        roles=[{"company_id": "C0003", "person_id": "P1", "role": "UBO", "pct": 60.0},
               {"company_id": "C0001", "person_id": "P2", "role": "DIRECTOR", "pct": None},
               {"company_id": "C0005", "person_id": "P2", "role": "DIRECTOR", "pct": None},
               {"company_id": "C0004", "person_id": "P3", "role": "DIRECTOR", "pct": None}],
        corp_owners=[{"owner_company_id": "C0003", "owned_company_id": "C0001", "pct": 80.0},
                     {"owner_company_id": "C0003", "owned_company_id": "C0002", "pct": 75.0}],
        addresses=[],
        transactions=[{"txn_id": "T1", "company_id": "C0002", "bank_id": "BANK_B", "txn_date": None,
                       "amount": 1.0, "currency": "INR", "counterparty": "x", "txn_type": "LC"}],
        policy_clauses=[{"clause_id": "TF-4.2", "section": "4", "title": "t", "text": "x", "tags": ["hold"]},
                        {"clause_id": "ZZ-1", "section": "1", "title": "t", "text": "x", "tags": ["other"]}])
    return st


def make_match(counterparty_reg, match_type=MatchType.EXACT, pledged=T0, bank="BANK_A"):
    t = tok(counterparty_reg) if counterparty_reg else "0" * 64
    e = ConsortiumEntry("E1", bank, {"exact": "h"}, 10, t, pledged)
    return ConsortiumMatch("R1", e, match_type, ["exact" if match_type == MatchType.EXACT else "cargo"],
                           1.0 if match_type == MatchType.EXACT else 0.9, Citation(CitationKind.CONSORTIUM, "E1"))


def req(borrower, days=5):
    return FinancingRequest("R1", "BANK_B", borrower, 1000.0, "INR", T0 + timedelta(days=days))


def test_resolve_borrower():
    st = make_store()
    assert resolve_borrower(tok("REG-C0003"), st, SALT) == "C0003"
    assert resolve_borrower("deadbeef", st, SALT) is None


def test_shell_cluster_via_holding_exact_high():
    st = make_store()
    inv = investigate(req("C0002"), make_match("REG-C0001"), st, S)
    assert inv.counterparty_company_id == "C0001"
    assert inv.shared_ubos == ["P1"]  # via parent holding C0003
    assert inv.paths and len(inv.paths[0].edges) == 2
    assert all(e.citation.kind == CitationKind.REGISTRY for p in inv.paths for e in p.edges)
    assert any(p.edges and all(e.relation == "OWNS" for e in p.edges) for p in inv.paths)
    assert inv.timing_overlap_days == 5
    assert [t["txn_id"] for t in inv.transactions] == ["T1"]
    assert [c["clause_id"] for c in inv.policy_clauses] == ["TF-4.2"]
    conf = score(inv, S)
    assert conf.band == ConfidenceBand.HIGH and conf.score == 1.0
    ids = {e.rule_id for e in conf.evidence}
    assert {"R_EXACT_HASH", "R_SHARED_UBO", "R_CORP_OWNERSHIP", "R_TIMING_OVERLAP"} <= ids
    assert all(e.citations for e in conf.evidence)


def test_fuzzy_no_link_low_with_missing():
    st = make_store()
    inv = investigate(req("C0004", days=10), make_match("REG-C0001", MatchType.FUZZY), st, S)
    assert inv.paths == [] and not inv.shared_directors
    conf = score(inv, S)
    assert conf.band == ConfidenceBand.LOW
    assert any("original B/L" in m for m in conf.missing)
    assert any("UBO declaration" in m for m in conf.missing)


def test_fuzzy_shared_director_timing_high():
    st = make_store()
    inv = investigate(req("C0005", days=3), make_match("REG-C0001", MatchType.FUZZY), st, S)
    assert inv.shared_directors == ["P2"]
    assert inv.paths and len(inv.paths[0].edges) == 2
    conf = score(inv, S)
    assert conf.band == ConfidenceBand.HIGH
    assert abs(conf.score - 0.65) < 1e-9
    assert all(e.citations for e in conf.evidence)


def test_same_borrower_identity_rules():
    st = make_store()
    inv = investigate(req("C0001"), make_match("REG-C0001"), st, S)
    assert inv.counterparty_company_id == inv.borrower_id == "C0001"
    conf = score(inv, S)
    ids = [e.rule_id for e in conf.evidence]
    assert "R_SHARED_UBO" in ids and "R_SHARED_DIRECTOR" in ids
    ubo = next(e for e in conf.evidence if e.rule_id == "R_SHARED_UBO")
    assert "same legal entity" in ubo.description.lower()
    assert any(c.kind == CitationKind.RULE for c in ubo.citations)
    assert conf.band == ConfidenceBand.HIGH


def test_unresolved_token():
    st = make_store()
    inv = investigate(req("C0002", days=90), make_match(None, MatchType.FUZZY), st, S)
    assert inv.counterparty_company_id is None and inv.paths == []
    conf = score(inv, S)
    assert conf.band == ConfidenceBand.LOW
    assert any("Confirm counterparty identity" in m for m in conf.missing)


def test_same_phone_shared():
    st = make_store()
    inv = investigate(req("C0006"), make_match("REG-C0001"), st, S)
    assert inv.shared_phones == ["+91-C0001"]
    assert any(e.rule_id == "R_SAME_PHONE" for e in score(inv, S).evidence)
