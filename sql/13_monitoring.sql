-- sql/13_monitoring.sql — authored by Snowflake Cortex Code (CoCo)
-- ============================================================================
-- Suraksha monitoring layer: events table, pipeline health view, data quality
-- view, and three scheduled alerts that INSERT into MON_EVENTS.
--
-- Prerequisites: sql/00..05, sql/11 (RUN_PIPELINE_BATCH) already deployed.
-- Run as ACCOUNTADMIN for the two GRANT statements; everything else as
-- SURAKSHA_ADMIN.  Idempotent (CREATE OR REPLACE / IF NOT EXISTS).
-- ============================================================================

-- ============================================================================
-- SECTION A : ACCOUNT-LEVEL GRANTS (ACCOUNTADMIN)
-- ============================================================================
USE ROLE ACCOUNTADMIN;

-- SURAKSHA_ADMIN needs ACCOUNT_USAGE to power the pipeline health view.
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE SURAKSHA_ADMIN;

-- SURAKSHA_ADMIN needs EXECUTE ALERT to own and resume alerts.
GRANT EXECUTE ALERT ON ACCOUNT TO ROLE SURAKSHA_ADMIN;

-- ============================================================================
-- SECTION B : MONITORING OBJECTS (SURAKSHA_ADMIN)
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

-- ---- 1. Events table: all alert firings land here.
CREATE TABLE IF NOT EXISTS SURAKSHA.CORE.MON_EVENTS (
  event_time  TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
  severity    VARCHAR       NOT NULL COMMENT 'CRITICAL | HIGH | MEDIUM | LOW',
  source      VARCHAR       NOT NULL COMMENT 'alert name or manual',
  check_name  VARCHAR       NOT NULL,
  detail      VARIANT
);

-- ---- 2. Pipeline health: recent CALL history with p50/p95 durations.
--    Uses SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY (up to 45 min latency).
CREATE OR REPLACE VIEW SURAKSHA.CORE.MON_PIPELINE_HEALTH AS
WITH proc_calls AS (
  SELECT
    query_text,
    CASE
      WHEN query_text ILIKE '%RUN_PIPELINE_BATCH%' THEN 'RUN_PIPELINE_BATCH'
      WHEN query_text ILIKE '%RUN_PIPELINE%'       THEN 'RUN_PIPELINE'
      WHEN query_text ILIKE '%LOAD_SYNTH%'         THEN 'LOAD_SYNTH'
      WHEN query_text ILIKE '%DECIDE_CASE%'        THEN 'DECIDE_CASE'
      WHEN query_text ILIKE '%SCREEN_INBOX%'       THEN 'SCREEN_INBOX'
    END AS procedure_name,
    start_time,
    end_time,
    total_elapsed_time                              AS duration_ms,
    execution_status,
    error_message
  FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
  WHERE query_type = 'CALL'
    AND database_name = 'SURAKSHA'
    AND (   query_text ILIKE '%RUN_PIPELINE_BATCH%'
         OR query_text ILIKE '%RUN_PIPELINE%'
         OR query_text ILIKE '%LOAD_SYNTH%'
         OR query_text ILIKE '%DECIDE_CASE%'
         OR query_text ILIKE '%SCREEN_INBOX%')
    AND start_time >= DATEADD('day', -7, CURRENT_TIMESTAMP())
),
stats AS (
  SELECT
    procedure_name,
    APPROX_PERCENTILE(duration_ms, 0.50) AS p50_ms,
    APPROX_PERCENTILE(duration_ms, 0.95) AS p95_ms
  FROM proc_calls
  WHERE execution_status = 'SUCCESS'
  GROUP BY procedure_name
)
SELECT
  c.procedure_name,
  c.start_time,
  c.end_time,
  c.duration_ms,
  c.execution_status,
  c.error_message,
  s.p50_ms,
  s.p95_ms
FROM proc_calls c
LEFT JOIN stats s ON s.procedure_name = c.procedure_name
ORDER BY c.start_time DESC;

-- ---- 3. Data quality: missing fields, bad ledger rows, stale pending cases.
CREATE OR REPLACE VIEW SURAKSHA.CORE.MON_DATA_QUALITY AS
WITH missing_fields AS (
  SELECT
    COUNT_IF(bl_number IS NULL OR bl_number = '')   AS missing_bl_number,
    COUNT_IF(vessel IS NULL OR vessel = '')          AS missing_vessel,
    COUNT_IF(voyage IS NULL OR voyage = '')          AS missing_voyage,
    COUNT_IF(commodity IS NULL OR commodity = '')    AS missing_commodity,
    COUNT_IF(quantity IS NULL)                       AS missing_quantity,
    COUNT(*)                                        AS total_requests
  FROM SURAKSHA.CORE.REQUEST_FIELDS
),
bad_ledger AS (
  SELECT
    COUNT_IF(keys IS NULL)                          AS missing_keys,
    COUNT(*)                                        AS total_ledger_rows
  FROM SURAKSHA.CONSORTIUM.LEDGER
),
stale_cases AS (
  SELECT
    COUNT(*)                                        AS pending_over_24h
  FROM SURAKSHA.CORE.CASES
  WHERE status = 'PENDING_APPROVAL'
    AND decided_at IS NULL
    AND case_id IN (
      SELECT 'CASE-' || request_id
      FROM SURAKSHA.CORE.REPORTS
      WHERE created_at < DATEADD('hour', -24, CURRENT_TIMESTAMP())
    )
)
SELECT
  f.total_requests,
  f.missing_bl_number,
  f.missing_vessel,
  f.missing_voyage,
  f.missing_commodity,
  f.missing_quantity,
  l.total_ledger_rows,
  l.missing_keys,
  s.pending_over_24h
FROM missing_fields f, bad_ledger l, stale_cases s;

-- ============================================================================
-- SECTION C : ALERTS (every 5 minutes, warehouse SURAKSHA_WH)
-- ============================================================================

-- ---- Alert 1: CRITICAL — audit hash chain broken.
CREATE OR REPLACE ALERT SURAKSHA.CORE.ALERT_AUDIT_CHAIN_BROKEN
  WAREHOUSE = SURAKSHA_WH
  SCHEDULE  = '5 MINUTE'
  IF (EXISTS (
    SELECT 1 FROM SURAKSHA.CORE.V_AUDIT_VERIFY_SUMMARY
    WHERE chain_intact = FALSE
  ))
  THEN
    INSERT INTO SURAKSHA.CORE.MON_EVENTS (severity, source, check_name, detail)
    SELECT 'CRITICAL',
           'ALERT_AUDIT_CHAIN_BROKEN',
           'audit_chain_integrity',
           OBJECT_CONSTRUCT('rows_checked', rows_checked, 'breaks', breaks, 'first_break_seq', first_break_seq)
    FROM SURAKSHA.CORE.V_AUDIT_VERIFY_SUMMARY;

-- ---- Alert 2: HIGH — a Suraksha stored procedure genuinely failed in the last hour.
--    DECIDE_CASE refusals (non-human actor, missing reject reason, already decided)
--    are the G4 control working, so they are excluded. All other DECIDE_CASE errors,
--    and every error from LOAD_SYNTH / RUN_PIPELINE(_BATCH) / SCREEN_INBOX, still count.
CREATE OR REPLACE ALERT SURAKSHA.CORE.ALERT_PROC_FAILURE
  WAREHOUSE = SURAKSHA_WH
  SCHEDULE  = '5 MINUTE'
  IF (EXISTS (
    SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
    WHERE query_type = 'CALL'
      AND database_name = 'SURAKSHA'
      AND execution_status = 'FAIL'
      AND start_time >= DATEADD('hour', -1, CURRENT_TIMESTAMP())
      AND (   query_text ILIKE '%RUN_PIPELINE_BATCH%'
           OR query_text ILIKE '%RUN_PIPELINE%'
           OR query_text ILIKE '%LOAD_SYNTH%'
           OR query_text ILIKE '%DECIDE_CASE%'
           OR query_text ILIKE '%SCREEN_INBOX%')
      AND NOT (    query_text ILIKE '%DECIDE_CASE%'
               AND (   error_message ILIKE '%named human officer%'
                    OR error_message ILIKE '%reason is required to reject%'
                    OR error_message ILIKE '%already decided%'))
  ))
  THEN
    INSERT INTO SURAKSHA.CORE.MON_EVENTS (severity, source, check_name, detail)
    SELECT 'HIGH',
           'ALERT_PROC_FAILURE',
           'procedure_failure',
           OBJECT_CONSTRUCT('procedure', query_text, 'status', execution_status,
                            'error', error_message, 'start_time', start_time::STRING)
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
    WHERE query_type = 'CALL'
      AND database_name = 'SURAKSHA'
      AND execution_status = 'FAIL'
      AND start_time >= DATEADD('hour', -1, CURRENT_TIMESTAMP())
      AND (   query_text ILIKE '%RUN_PIPELINE_BATCH%'
           OR query_text ILIKE '%RUN_PIPELINE%'
           OR query_text ILIKE '%LOAD_SYNTH%'
           OR query_text ILIKE '%DECIDE_CASE%'
           OR query_text ILIKE '%SCREEN_INBOX%')
      AND NOT (    query_text ILIKE '%DECIDE_CASE%'
               AND (   error_message ILIKE '%named human officer%'
                    OR error_message ILIKE '%reason is required to reject%'
                    OR error_message ILIKE '%already decided%'));

-- ---- Alert 3: MEDIUM — cases stuck in PENDING_APPROVAL > 24 hours.
CREATE OR REPLACE ALERT SURAKSHA.CORE.ALERT_STALE_PENDING_CASES
  WAREHOUSE = SURAKSHA_WH
  SCHEDULE  = '5 MINUTE'
  IF (EXISTS (
    SELECT 1 FROM SURAKSHA.CORE.MON_DATA_QUALITY
    WHERE pending_over_24h > 0
  ))
  THEN
    INSERT INTO SURAKSHA.CORE.MON_EVENTS (severity, source, check_name, detail)
    SELECT 'MEDIUM',
           'ALERT_STALE_PENDING_CASES',
           'stale_pending_cases',
           OBJECT_CONSTRUCT('pending_over_24h', pending_over_24h)
    FROM SURAKSHA.CORE.MON_DATA_QUALITY;

-- ============================================================================
-- SECTION D : RESUME ALERTS
-- ============================================================================
ALTER ALERT SURAKSHA.CORE.ALERT_AUDIT_CHAIN_BROKEN  RESUME;
ALTER ALERT SURAKSHA.CORE.ALERT_PROC_FAILURE         RESUME;
ALTER ALERT SURAKSHA.CORE.ALERT_STALE_PENDING_CASES  RESUME;

-- ============================================================================
-- SECTION E : GRANTS
-- ============================================================================
GRANT SELECT ON TABLE SURAKSHA.CORE.MON_EVENTS          TO ROLE SURAKSHA_APP;
GRANT SELECT ON VIEW  SURAKSHA.CORE.MON_PIPELINE_HEALTH TO ROLE SURAKSHA_APP;
GRANT SELECT ON VIEW  SURAKSHA.CORE.MON_DATA_QUALITY    TO ROLE SURAKSHA_APP;
GRANT SELECT ON TABLE SURAKSHA.CORE.MON_EVENTS          TO ROLE SURAKSHA_AUDITOR;
GRANT SELECT ON VIEW  SURAKSHA.CORE.MON_PIPELINE_HEALTH TO ROLE SURAKSHA_AUDITOR;
GRANT SELECT ON VIEW  SURAKSHA.CORE.MON_DATA_QUALITY    TO ROLE SURAKSHA_AUDITOR;
