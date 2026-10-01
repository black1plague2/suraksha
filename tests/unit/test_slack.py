import hashlib
import hmac
import json
import urllib.request
from datetime import datetime, timezone

import pytest

from suraksha.agents.approval import AuditLog, open_case
from suraksha.integrations.slack import (
    build_approval_message, handle_action, post_for_approval, verify_signature,
)
from suraksha.models import (
    CaseStatus, Citation, CitationKind, ReportSection, ReportSentence, STRDraft,
)
from suraksha.store.memory import MemoryStore


def draft(rid="R1"):
    c = [Citation(CitationKind.RULE, "R")]
    p5 = ReportSection("PART 5", "Grounds", [
        ReportSentence("Ground one.", c), ReportSentence("Ground two.", c),
        ReportSentence("The deterministic confidence score is 0.80 (HIGH).", c),
        ReportSentence("Ground three.", c), ReportSentence("Ground four.", c)])
    return STRDraft("STR-" + rid, rid, "BANK_A", datetime.now(timezone.utc), [p5], "HOLD_DISBURSEMENT")


@pytest.fixture
def env():
    st = MemoryStore()
    a = AuditLog(st)
    return st, a, open_case(draft(), st, a)


def test_message_structure(env):
    _, _, case = env
    m = build_approval_message(case, draft())
    types = [b["type"] for b in m["blocks"]]
    assert types[0] == "header" and "actions" in types
    flat = json.dumps(m)
    assert "R1" in flat and "BANK_A" in flat and "0.80 (HIGH)" in flat and "HOLD_DISBURSEMENT" in flat
    assert "Ground three" in flat and "Ground four" not in flat  # top 3 only, score sentence skipped
    btns = [e for b in m["blocks"] if b["type"] == "actions" for e in b["elements"]]
    assert {e["action_id"] for e in btns} == {"suraksha_approve", "suraksha_reject"}
    assert all(e["value"] == case.case_id for e in btns)


def test_handle_approve(env):
    st, a, case = env
    out = handle_action({"user": {"name": "priya"},
                         "actions": [{"action_id": "suraksha_approve", "value": case.case_id}]}, st, a)
    assert out.status == CaseStatus.FILED and out.decided_by == "officer:priya"
    assert a.verify()


def test_handle_reject_default_and_state_reason(env):
    st, a, case = env
    out = handle_action({"user": {"username": "raj"},
                         "actions": [{"action_id": "suraksha_reject", "value": case.case_id}]}, st, a)
    assert out.status == CaseStatus.CLOSED and out.reason == "Rejected via Slack"
    case2 = open_case(draft("R2"), st, a)
    out2 = handle_action({"user": {"name": "raj"},
                          "state": {"values": {"b": {"a": {"value": "Duplicate of own deal"}}}},
                          "actions": [{"action_id": "suraksha_reject", "value": case2.case_id}]}, st, a)
    assert out2.reason == "Duplicate of own deal"


def test_handle_bad_payloads(env):
    st, a, case = env
    with pytest.raises(ValueError):
        handle_action({"actions": []}, st, a)
    with pytest.raises(ValueError):
        handle_action({"user": {"name": "x"}, "actions": [{"action_id": "other", "value": case.case_id}]}, st, a)
    with pytest.raises(ValueError):  # no user
        handle_action({"actions": [{"action_id": "suraksha_approve", "value": case.case_id}]}, st, a)


def test_verify_signature():
    secret, ts, body = "s3cret", "1700000000", "payload=%7B%7D"
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    assert verify_signature(secret, ts, body, sig)
    assert verify_signature(secret, ts, body.encode(), sig)
    assert not verify_signature("wrong", ts, body, sig)
    assert not verify_signature(secret, ts, body + "x", sig)
    assert not verify_signature(secret, ts, body, "")
    assert not verify_signature(secret, ts, body, sig, now=1700001000, max_age_s=300)
    assert verify_signature(secret, ts, body, sig, now=1700000100, max_age_s=300)


def test_post_none_url_false(env):
    _, _, case = env
    assert post_for_approval(case, draft(), None) is False
    assert post_for_approval(case, draft(), "") is False


def test_post_success_and_failure(env, monkeypatch):
    _, _, case = env
    seen = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def ok(req, timeout=None):
        seen["timeout"], seen["url"] = timeout, req.full_url
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", ok)
    assert post_for_approval(case, draft(), "https://hooks.example/x") is True
    assert seen["timeout"] == 5

    def boom(req, timeout=None):
        raise OSError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert post_for_approval(case, draft(), "https://hooks.example/x") is False
