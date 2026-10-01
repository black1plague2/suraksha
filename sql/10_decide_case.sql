-- ============================================================================
-- Suraksha 10_decide_case.sql : gate G4 (human approval) enforced IN SQL.
-- SURAKSHA.CORE.DECIDE_CASE(case_id, decision, officer, reason) runs with OWNER's rights
-- (SURAKSHA_ADMIN).  SURAKSHA_APP has no UPDATE/DELETE on CASES, so the ONLY way a case can
-- move out of PENDING_APPROVAL is this procedure, which mirrors agents/approval.py decide():
--   * officer non-blank and not 'system:*'  (a leading 'officer:' is stripped)
--   * decision APPROVE | REJECT; REJECT needs a reason
--   * case must be PENDING_APPROVAL
--   * APPROVE -> FILED + hold_recommended TRUE; audit CASE_APPROVED, CASE_FILED, HOLD_RECOMMENDED
--   * REJECT  -> CLOSED;                         audit CASE_REJECTED
-- Audit rows: seq = max+1, hash = sha256(prev_hash + canonical_json) with the same json.dumps
-- arguments as approval._canonical.  `at` is naive UTC (the stored TIMESTAMP_NTZ), so the hash
-- matches what a Python verify() recomputes after reading the row back.
-- Run as SURAKSHA_ADMIN.  Idempotent.
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

-- G4: the app role may never change a case directly (re-asserted in case 01 was re-run out of order)
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE SURAKSHA.CORE.CASES FROM ROLE SURAKSHA_APP;

CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.DECIDE_CASE(CASE_ID STRING, DECISION STRING, OFFICER STRING, REASON STRING)
  RETURNS STRING
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.12'
  PACKAGES = ('snowflake-snowpark-python')
  HANDLER = 'run'
  EXECUTE AS OWNER
AS
$$
import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64


def _canonical(seq, at, actor, action, subject_id, payload):
    # identical to suraksha.agents.approval._canonical
    return json.dumps(
        {"seq": seq, "at": at, "actor": actor, "action": action, "subject_id": subject_id, "payload": payload},
        sort_keys=True, separators=(",", ":"), default=str,
    )


def _hash(prev_hash, canonical):
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def _officer(officer):
    name = (officer or "").strip()
    if name.lower().startswith("system:"):
        raise ValueError("decisions must be made by a named human officer, not a system actor")
    if name.lower().startswith("officer:"):
        name = name[len("officer:"):].strip()
    if not name:
        raise ValueError("officer name is required")
    return name


def _append(session, actor, action, subject_id, payload):
    rows = session.sql(
        "SELECT seq, entry_hash FROM SURAKSHA.CORE.AUDIT_LOG ORDER BY seq DESC LIMIT 1").collect()
    seq = int(rows[0]["SEQ"]) + 1 if rows else 1
    prev = rows[0]["ENTRY_HASH"] if rows else GENESIS
    at = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC == stored TIMESTAMP_NTZ
    entry = _hash(prev, _canonical(seq, at, actor, action, subject_id, payload))
    session.sql(
        "INSERT INTO SURAKSHA.CORE.AUDIT_LOG (seq, at, actor, action, subject_id, payload, prev_hash, entry_hash) "
        "SELECT ?, TO_TIMESTAMP_NTZ(?), ?, ?, ?, PARSE_JSON(?), ?, ?",
        params=[seq, at.isoformat(sep=" "), actor, action, subject_id,
                json.dumps(payload, sort_keys=True, default=str), prev, entry],
    ).collect()


def run(session, case_id, decision, officer, reason):
    name = _officer(officer)
    dec = (decision or "").strip().upper()
    if dec not in ("APPROVE", "REJECT"):
        raise ValueError("decision must be APPROVE or REJECT")
    reason = (reason or "").strip()
    if dec == "REJECT" and not reason:
        raise ValueError("a reason is required to reject")
    rows = session.sql(
        "SELECT status, request_id, report_id FROM SURAKSHA.CORE.CASES WHERE case_id = ?", params=[case_id]).collect()
    if not rows:
        raise ValueError("unknown case " + str(case_id))
    status = rows[0]["STATUS"]
    if status != "PENDING_APPROVAL":
        raise ValueError("case " + str(case_id) + " already decided (" + str(status) + ")")
    actor = "officer:" + name
    new_status, hold = ("FILED", True) if dec == "APPROVE" else ("CLOSED", False)
    session.sql("BEGIN").collect()
    try:
        n = session.sql(
            "UPDATE SURAKSHA.CORE.CASES SET status = ?, hold_recommended = ?, decided_by = ?, "
            "decided_at = CURRENT_TIMESTAMP()::TIMESTAMP_NTZ, reason = NULLIF(?, '') "
            "WHERE case_id = ? AND status = 'PENDING_APPROVAL'",
            params=[new_status, hold, actor, reason, case_id]).collect()
        if int(n[0][0]) != 1:  # lost a race with another decision
            raise ValueError("case " + str(case_id) + " is no longer pending")
        if dec == "APPROVE":
            _append(session, actor, "CASE_APPROVED", case_id, {"reason": reason})
            _append(session, actor, "CASE_FILED", case_id, {"report_id": rows[0]["REPORT_ID"]})
            _append(session, actor, "HOLD_RECOMMENDED", case_id, {"request_id": rows[0]["REQUEST_ID"]})
        else:
            _append(session, actor, "CASE_REJECTED", case_id, {"reason": reason})
        session.sql("COMMIT").collect()
    except Exception:
        session.sql("ROLLBACK").collect()
        raise
    return json.dumps({"case_id": case_id, "status": new_status, "hold_recommended": hold, "decided_by": actor})
$$;

GRANT USAGE ON PROCEDURE SURAKSHA.CORE.DECIDE_CASE(STRING, STRING, STRING, STRING) TO ROLE SURAKSHA_APP;

-- usage (as SURAKSHA_APP):  CALL SURAKSHA.CORE.DECIDE_CASE('<case_id>', 'APPROVE', 'Priya Nair', '');
