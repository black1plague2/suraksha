-- ============================================================================
-- Suraksha 03_audit_immutability.sql : append-only, hash-chained audit log.
-- Run as SURAKSHA_ADMIN.
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE DATABASE SURAKSHA;
USE SCHEMA CORE;

CREATE TABLE IF NOT EXISTS AUDIT_LOG (
  seq        NUMBER(18,0) NOT NULL COMMENT 'contiguous from 1 (or 0); gaps = deleted rows',
  at         TIMESTAMP_NTZ NOT NULL,
  actor      VARCHAR NOT NULL COMMENT 'system:<agent> | officer:<name>',
  action     VARCHAR NOT NULL,
  subject_id VARCHAR NOT NULL,
  payload    VARIANT,
  prev_hash  VARCHAR(64) NOT NULL COMMENT 'entry_hash of previous row; 64 zeros at genesis',
  entry_hash VARCHAR(64) NOT NULL COMMENT 'sha256(prev_hash + canonical_json(rest)), computed by the app',
  written_by VARCHAR DEFAULT CURRENT_USER() COMMENT 'Snowflake user that inserted the row',
  written_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
  PRIMARY KEY (seq)
) COMMENT = 'Append-only. App role has INSERT + SELECT only.';

-- ---- insert-only privileges.  NEVER grant UPDATE, DELETE, TRUNCATE or OWNERSHIP to the app role.
REVOKE ALL PRIVILEGES ON TABLE AUDIT_LOG FROM ROLE SURAKSHA_APP;
GRANT INSERT, SELECT ON TABLE AUDIT_LOG TO ROLE SURAKSHA_APP;
GRANT SELECT ON TABLE AUDIT_LOG TO ROLE SURAKSHA_AUDITOR;
-- Bank roles get nothing on the audit log.

-- ---- verification view.  Flags every break in the chain:
--   * BAD_GENESIS      first row's prev_hash is not 64 zeros
--   * BROKEN_LINK      prev_hash != previous row's entry_hash   (row edited, replaced or removed)
--   * SEQ_GAP          seq is not previous seq + 1               (row deleted)
--   * BAD_HASH_FORMAT  entry_hash is not 64 lowercase hex chars
-- LAG() looks at the previous row ordered by seq.
CREATE OR REPLACE VIEW V_AUDIT_VERIFY AS
WITH ordered AS (
  SELECT
    seq, at, actor, action, subject_id, prev_hash, entry_hash,
    LAG(entry_hash) OVER (ORDER BY seq) AS expected_prev_hash,
    LAG(seq)        OVER (ORDER BY seq) AS prev_seq
  FROM AUDIT_LOG
)
SELECT
  seq, at, actor, action, subject_id, prev_hash, entry_hash, expected_prev_hash,
  CASE
    WHEN expected_prev_hash IS NULL AND prev_hash <> REPEAT('0', 64)      THEN 'BAD_GENESIS'
    WHEN expected_prev_hash IS NOT NULL AND prev_hash <> expected_prev_hash THEN 'BROKEN_LINK'
    WHEN prev_seq IS NOT NULL AND seq <> prev_seq + 1                      THEN 'SEQ_GAP'
    WHEN NOT REGEXP_LIKE(entry_hash, '^[0-9a-f]{64}$')                     THEN 'BAD_HASH_FORMAT'
    ELSE 'OK'
  END AS chain_status
FROM ordered;

-- One-line verdict: SELECT * FROM V_AUDIT_VERIFY_SUMMARY;
CREATE OR REPLACE VIEW V_AUDIT_VERIFY_SUMMARY AS
SELECT
  COUNT(*)                                          AS rows_checked,
  COUNT_IF(chain_status <> 'OK')                    AS breaks,
  MIN(IFF(chain_status <> 'OK', seq, NULL))         AS first_break_seq,
  COUNT_IF(chain_status <> 'OK') = 0                AS chain_intact
FROM V_AUDIT_VERIFY;

GRANT SELECT ON VIEW V_AUDIT_VERIFY         TO ROLE SURAKSHA_AUDITOR;
GRANT SELECT ON VIEW V_AUDIT_VERIFY_SUMMARY TO ROLE SURAKSHA_AUDITOR;
GRANT SELECT ON VIEW V_AUDIT_VERIFY         TO ROLE SURAKSHA_APP;
GRANT SELECT ON VIEW V_AUDIT_VERIFY_SUMMARY TO ROLE SURAKSHA_APP;

-- LIMITATION: this view proves LINKAGE (no row removed/reordered/relinked) but cannot recompute each
-- entry_hash because the canonical JSON used by agents/approval.py is produced in Python. Full content
-- re-verification is AuditLog.verify() in Python (reads via SnowflakeStore.list_audit()). Truncating the
-- tail of the log is detectable only by comparing against an externally-held last hash.
--
-- Defence in depth (optional, account-level features; verify availability for your edition):
--   * Time Travel / Fail-safe retain prior table states:  SELECT * FROM AUDIT_LOG AT(OFFSET => -3600);
--   * Snowflake "immutable" retention features (e.g. Time Travel retention + Trust Center / access
--     history) can be layered on; ACCESS_HISTORY in SNOWFLAKE.ACCOUNT_USAGE records every read/write.
--   * Demo of tamper evidence (run as ADMIN, NOT as the app role):
--       DELETE FROM AUDIT_LOG WHERE seq = 3;   -- as the app role: "insufficient privileges"
--       -- as ADMIN (simulating a privileged insider): the delete is allowed but then
--       SELECT * FROM V_AUDIT_VERIFY_SUMMARY;  -- shows SEQ_GAP / BROKEN_LINK at seq 4
