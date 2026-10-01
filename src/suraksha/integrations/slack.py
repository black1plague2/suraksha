"""Slack approval integration (Block Kit + interactive payload handling).

SECURITY: in production the HTTP endpoint receiving Slack interactive payloads MUST verify the
request signature (X-Slack-Signature / X-Slack-Request-Timestamp) with `verify_signature` over the
RAW request body before calling `handle_action`; otherwise anyone could forge approvals.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.request
from typing import Any

from suraksha.agents.approval import AuditLog, decide
from suraksha.log import get_logger
from suraksha.models import Case, ConfidenceResult, Decision, STRDraft

log = get_logger(__name__)

APPROVE_ID = "suraksha_approve"
REJECT_ID = "suraksha_reject"
_SCORE_RE = re.compile(r"score is ([0-9.]+) \((\w+)\)")


def _score_band(report: STRDraft, confidence: ConfidenceResult | None) -> tuple[str, str]:
    if confidence is not None:
        return f"{confidence.score:.2f}", confidence.band.value
    for sec in report.sections:
        for s in sec.sentences:
            m = _SCORE_RE.search(s.text)
            if m:
                return m.group(1), m.group(2)
    return "n/a", "n/a"


def _top_grounds(report: STRDraft, n: int = 3) -> list[str]:
    for sec in report.sections:
        if sec.part == "PART 5":
            return [s.text for s in sec.sentences if not _SCORE_RE.search(s.text)][:n]
    return []


def build_approval_message(case: Case, report: STRDraft, confidence: ConfidenceResult | None = None) -> dict:
    score, band = _score_band(report, confidence)
    grounds = _top_grounds(report)
    grounds_txt = "\n".join(f"- {g[:280]}" for g in grounds) or "No grounds available"
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Suraksha: duplicate financing alert"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Request:*\n{case.request_id}"},
            {"type": "mrkdwn", "text": f"*Borrower bank:*\n{report.reporting_bank_id}"},
            {"type": "mrkdwn", "text": f"*Confidence:*\n{score} ({band})"},
            {"type": "mrkdwn", "text": f"*Recommended action:*\n{report.recommended_action}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Top grounds*\n{grounds_txt}"}},
        {"type": "actions", "block_id": "suraksha_actions", "elements": [
            {"type": "button", "action_id": APPROVE_ID, "style": "primary", "value": case.case_id,
             "text": {"type": "plain_text", "text": "Approve"}},
            {"type": "button", "action_id": REJECT_ID, "style": "danger", "value": case.case_id,
             "text": {"type": "plain_text", "text": "Reject"}},
        ]},
    ]
    return {"text": f"Suraksha alert for {case.request_id}: approval required", "blocks": blocks}


def post_for_approval(case: Case, report: STRDraft, webhook_url: str | None,
                      confidence: ConfidenceResult | None = None) -> bool:
    """POST to a Slack webhook. Never raises; returns False (and logs) on any failure."""
    if not webhook_url:
        log.info("slack_skipped_no_webhook", extra={"ctx": {"case_id": case.case_id}})
        return False
    try:
        body = json.dumps(build_approval_message(case, report, confidence)).encode("utf-8")
        req = urllib.request.Request(webhook_url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            ok = 200 <= getattr(resp, "status", 200) < 300
        log.info("slack_posted", extra={"ctx": {"case_id": case.case_id, "ok": ok}})
        return ok
    except Exception as exc:
        log.warning("slack_post_failed", extra={"ctx": {"case_id": case.case_id, "error": str(exc)}})
        return False


def _reason_from_state(state: Any) -> str:
    """Find the first non-empty text input value in a Slack `state` object."""
    values = state.get("values") if isinstance(state, dict) else None
    if isinstance(values, dict):
        for block in values.values():
            if isinstance(block, dict):
                for el in block.values():
                    if isinstance(el, dict) and isinstance(el.get("value"), str) and el["value"].strip():
                        return el["value"].strip()
    return ""


def handle_action(payload: dict, store: Any, audit: AuditLog) -> Case:
    """Map a (signature-verified) Slack interactive payload to approval.decide()."""
    actions = payload.get("actions") or []
    if not actions:
        raise ValueError("payload has no actions")
    action = actions[0]
    action_id, case_id = action.get("action_id"), action.get("value")
    user = payload.get("user") or {}
    officer = user.get("name") or user.get("username") or ""
    if action_id == APPROVE_ID:
        return decide(case_id, Decision.APPROVE, officer, "Approved via Slack", store, audit)
    if action_id == REJECT_ID:
        reason = _reason_from_state(payload.get("state")) or "Rejected via Slack"
        return decide(case_id, Decision.REJECT, officer, reason, store, audit)
    raise ValueError(f"unknown action_id {action_id!r}")


def verify_signature(signing_secret: str, timestamp: str, body: str | bytes, signature: str,
                     *, now: float | None = None, max_age_s: int | None = None) -> bool:
    """Slack v0 scheme: 'v0=' + HMAC-SHA256(secret, 'v0:{timestamp}:{body}'). Constant-time compare.
    If max_age_s is given, also rejects timestamps older than that (replay protection)."""
    try:
        if max_age_s is not None:
            if abs((now if now is not None else time.time()) - float(timestamp)) > max_age_s:
                return False
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        base = b"v0:" + str(timestamp).encode("utf-8") + b":" + raw
        expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")
    except Exception:
        return False
