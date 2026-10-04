"""Human approval workflow + tamper-evident audit log.

Hash chain: entry_hash = sha256(prev_hash + canonical_json({seq, at, actor, action, subject_id, payload})).
Genesis prev_hash is 64 zeros. Seq starts at 1 and is contiguous.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

from suraksha.log import get_logger
from suraksha.models import AuditRecord, Case, CaseStatus, Decision, STRDraft

log = get_logger(__name__)

GENESIS = "0" * 64


def _canon_at(at: datetime) -> str:
    """Timezone-neutral form: naive UTC, `str()` style. Snowflake stores TIMESTAMP_NTZ and returns naive values,
    so hashing the aware form ('+00:00') would make chains read back from Snowflake fail verification.
    Must match sql/10_decide_case.sql (which hashes a naive-UTC datetime via json default=str)."""
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    return str(at)


def _canonical(seq: int, at: datetime, actor: str, action: str, subject_id: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {"seq": seq, "at": _canon_at(at), "actor": actor, "action": action, "subject_id": subject_id, "payload": payload},
        sort_keys=True, separators=(",", ":"), default=str,
    )


def _hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, store: Any) -> None:
        self.store = store
        self._lock = threading.Lock()

    def append(self, actor: str, action: str, subject_id: str, payload: dict[str, Any]) -> AuditRecord:
        with self._lock:
            last = self.store.last_audit()
            seq = (last.seq + 1) if last else 1
            prev = last.entry_hash if last else GENESIS
            at = datetime.now(timezone.utc)
            payload = dict(payload or {})
            rec = AuditRecord(seq=seq, at=at, actor=actor, action=action, subject_id=subject_id,
                              payload=payload, prev_hash=prev,
                              entry_hash=_hash(prev, _canonical(seq, at, actor, action, subject_id, payload)))
            self.store.append_audit(rec)
        log.info("audit_append", extra={"ctx": {"seq": seq, "action": action, "subject_id": subject_id}})
        return rec

    def verify(self) -> bool:
        prev = GENESIS
        expected_seq = None
        for r in self.store.list_audit():
            if expected_seq is not None and r.seq != expected_seq:
                log.error("audit_seq_gap", extra={"ctx": {"seq": r.seq}})
                return False
            if r.prev_hash != prev:
                log.error("audit_chain_broken", extra={"ctx": {"seq": r.seq}})
                return False
            if r.entry_hash != _hash(prev, _canonical(r.seq, r.at, r.actor, r.action, r.subject_id, r.payload)):
                log.error("audit_tamper_detected", extra={"ctx": {"seq": r.seq}})
                return False
            prev = r.entry_hash
            expected_seq = r.seq + 1
        return True


def case_id_for(request_id: str) -> str:
    return "CASE-" + request_id


def open_case(report: STRDraft, store: Any, audit: AuditLog) -> Case:
    cid = case_id_for(report.request_id)
    existing = store.get_case(cid)
    if existing is not None:
        return existing
    case = Case(case_id=cid, request_id=report.request_id, report_id=report.report_id,
                status=CaseStatus.PENDING_APPROVAL, hold_recommended=False)
    store.save_report(report)
    store.save_case(case)
    audit.append("system:approval", "CASE_OPENED", cid,
                 {"request_id": report.request_id, "report_id": report.report_id})
    return case


def _normalise_officer(officer: str) -> str:
    name = (officer or "").strip()
    if name.lower().startswith("system:"):
        raise ValueError("decisions must be made by a named human officer, not a system actor")
    if name.lower().startswith("officer:"):
        name = name[len("officer:"):].strip()
    if not name:
        raise ValueError("officer name is required")
    return name


def decide(case_id: str, decision: Decision, officer: str, reason: str, store: Any, audit: AuditLog) -> Case:
    name = _normalise_officer(officer)
    decision = Decision(decision)
    reason = (reason or "").strip()
    if decision == Decision.REJECT and not reason:
        raise ValueError("a reason is required to reject")
    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"unknown case {case_id}")
    if case.status != CaseStatus.PENDING_APPROVAL:
        raise ValueError(f"case {case_id} already decided ({case.status.value})")
    actor = f"officer:{name}"
    now = datetime.now(timezone.utc)
    case.decided_by, case.decided_at = actor, now
    case.reason = reason or None
    if decision == Decision.APPROVE:
        case.status, case.hold_recommended = CaseStatus.FILED, True
        store.save_case(case)
        audit.append(actor, "CASE_APPROVED", case_id, {"reason": reason})
        audit.append(actor, "CASE_FILED", case_id, {"report_id": case.report_id})
        audit.append(actor, "HOLD_RECOMMENDED", case_id, {"request_id": case.request_id})
    else:
        case.status, case.hold_recommended = CaseStatus.CLOSED, False
        store.save_case(case)
        audit.append(actor, "CASE_REJECTED", case_id, {"reason": reason})
    log.info("case_decided", extra={"ctx": {"case_id": case_id, "decision": decision.value, "actor": actor}})
    return case
