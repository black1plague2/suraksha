from datetime import datetime, timezone

import pytest

from suraksha.agents.approval import AuditLog, decide, open_case
from suraksha.models import Citation, CitationKind, CaseStatus, Decision, ReportSection, ReportSentence, STRDraft
from suraksha.store.memory import MemoryStore


def draft(rid="R1"):
    s = ReportSentence("x", [Citation(CitationKind.RULE, "R_X")])
    return STRDraft("STR-" + rid, rid, "BANK_A", datetime.now(timezone.utc),
                    [ReportSection("PART 1", "t", [s])], "HOLD_DISBURSEMENT")


@pytest.fixture
def env():
    st = MemoryStore()
    return st, AuditLog(st)


def test_chain_and_verify(env):
    st, a = env
    assert a.verify()
    r1 = a.append("system:x", "A", "S1", {"k": 1})
    r2 = a.append("system:x", "B", "S1", {"k": datetime.now()})
    assert r1.prev_hash == "0" * 64 and r1.seq == 1
    assert r2.prev_hash == r1.entry_hash and r2.seq == 2
    assert a.verify()


def test_tamper_detected(env):
    st, a = env
    for i in range(3):
        a.append("system:x", "A", "S", {"i": i})
    st._audit[1].payload["i"] = 99
    assert a.verify() is False


def test_tamper_actor_and_delete_detected(env):
    st, a = env
    for i in range(3):
        a.append("system:x", "A", "S", {"i": i})
    st._audit[0].actor = "officer:Eve"
    assert not a.verify()
    st2 = MemoryStore()
    a2 = AuditLog(st2)
    for i in range(3):
        a2.append("system:x", "A", "S", {"i": i})
    del st2._audit[1]
    assert not a2.verify()


def test_open_case(env):
    st, a = env
    c = open_case(draft(), st, a)
    assert c.status == CaseStatus.PENDING_APPROVAL and c.hold_recommended is False
    assert st.get_case(c.case_id) is not None
    assert st.get_report("STR-R1") is not None
    assert [r.action for r in st.list_audit()] == ["CASE_OPENED"]


def test_approve(env):
    st, a = env
    c = open_case(draft(), st, a)
    out = decide(c.case_id, Decision.APPROVE, "Priya Nair", "", st, a)
    assert out.status == CaseStatus.FILED and out.hold_recommended
    assert out.decided_by == "officer:Priya Nair" and out.decided_at
    assert [r.action for r in st.list_audit()] == ["CASE_OPENED", "CASE_APPROVED", "CASE_FILED", "HOLD_RECOMMENDED"]
    assert a.verify()


def test_reject(env):
    st, a = env
    c = open_case(draft(), st, a)
    out = decide(c.case_id, Decision.REJECT, "Priya Nair", "False positive", st, a)
    assert out.status == CaseStatus.CLOSED and not out.hold_recommended
    assert out.reason == "False positive"
    assert st.list_audit()[-1].action == "CASE_REJECTED"


def test_reject_requires_reason(env):
    st, a = env
    c = open_case(draft(), st, a)
    with pytest.raises(ValueError):
        decide(c.case_id, Decision.REJECT, "Priya", "  ", st, a)
    assert st.get_case(c.case_id).status == CaseStatus.PENDING_APPROVAL


@pytest.mark.parametrize("who", ["system:auto", "", "   ", "officer:", "SYSTEM:bot"])
def test_bad_actor(env, who):
    st, a = env
    c = open_case(draft(), st, a)
    with pytest.raises(ValueError):
        decide(c.case_id, Decision.APPROVE, who, "", st, a)
    assert st.get_case(c.case_id).status == CaseStatus.PENDING_APPROVAL


def test_double_decision(env):
    st, a = env
    c = open_case(draft(), st, a)
    decide(c.case_id, Decision.APPROVE, "Priya", "", st, a)
    with pytest.raises(ValueError):
        decide(c.case_id, Decision.REJECT, "Raj", "changed mind", st, a)
    with pytest.raises(ValueError):
        decide(c.case_id, Decision.APPROVE, "Raj", "", st, a)


def test_unknown_case(env):
    st, a = env
    with pytest.raises(ValueError):
        decide("nope", Decision.APPROVE, "Priya", "", st, a)


def test_decide_works_when_case_status_comes_from_a_reloaded_module():
    """Live bug (Community Cloud): Streamlit reloaded suraksha.models while cached cases kept the OLD CaseStatus
    members, so `status is CaseStatus.PENDING_APPROVAL` was False and every case looked already decided."""
    import importlib
    from datetime import datetime, timezone
    import suraksha.models as models
    from suraksha.agents.approval import AuditLog, decide
    from suraksha.store.memory import MemoryStore
    old_pending = models.CaseStatus.PENDING_APPROVAL
    importlib.reload(models)
    assert old_pending is not models.CaseStatus.PENDING_APPROVAL      # the trap
    st = MemoryStore()
    st.save_case(models.Case("CASE-X", "X", "STR-X", old_pending, False))
    case = decide("CASE-X", "APPROVE", "Priya Nair", "ok", st, AuditLog(st))
    assert case.status == "FILED" and case.hold_recommended
