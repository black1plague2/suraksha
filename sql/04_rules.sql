-- ============================================================================
-- Suraksha 04_rules.sql : deterministic confidence rules as SQL.
-- MUST STAY IN SYNC with src/suraksha/config.py (RULE_WEIGHTS, confidence_threshold,
-- timing_window_days) and src/suraksha/agents/confidence.py. If you change one, change both.
-- Run as SURAKSHA_ADMIN.
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE DATABASE SURAKSHA;
USE SCHEMA CORE;

-- ---- parameters (single source inside Snowflake)
CREATE TABLE IF NOT EXISTS RULE_WEIGHTS (
  rule_id VARCHAR NOT NULL, weight NUMBER(5,4) NOT NULL, description VARCHAR,
  PRIMARY KEY (rule_id)
);
MERGE INTO RULE_WEIGHTS t
USING (SELECT * FROM VALUES
  ('R_EXACT_HASH',      0.50, 'identical cargo fingerprint pledged at another bank'),
  ('R_FUZZY_MATCH',     0.30, 'near-duplicate (reissued B/L, rounded qty)'),
  ('R_SHARED_UBO',      0.30, 'borrowers share an ultimate beneficial owner'),
  ('R_SHARED_DIRECTOR', 0.20, 'borrowers share a director'),
  ('R_CORP_OWNERSHIP',  0.25, 'one borrower owns (directly/indirectly) the other'),
  ('R_SAME_ADDRESS',    0.10, 'same registered address'),
  ('R_SAME_PHONE',      0.10, 'same phone number'),
  ('R_TIMING_OVERLAP',  0.15, 'second pledge within timing window of the first')
  AS s(rule_id, weight, description)) s
ON t.rule_id = s.rule_id
WHEN MATCHED THEN UPDATE SET weight = s.weight, description = s.description
WHEN NOT MATCHED THEN INSERT (rule_id, weight, description) VALUES (s.rule_id, s.weight, s.description);

CREATE TABLE IF NOT EXISTS RULE_PARAMS (param VARCHAR NOT NULL, value NUMBER(10,4) NOT NULL, PRIMARY KEY (param));
MERGE INTO RULE_PARAMS t
USING (SELECT * FROM VALUES
  ('confidence_threshold', 0.6),
  ('timing_window_days',   45)
  AS s(param, value)) s
ON t.param = s.param
WHEN MATCHED THEN UPDATE SET value = s.value
WHEN NOT MATCHED THEN INSERT (param, value) VALUES (s.param, s.value);

-- ---- investigation evidence: one row per request, written by the investigator step
-- (or derived by a loader from the Investigation dataclass).
CREATE TABLE IF NOT EXISTS INVESTIGATION_FACTS (
  request_id          VARCHAR NOT NULL,
  match_type          VARCHAR NOT NULL COMMENT 'EXACT | FUZZY',
  same_borrower       BOOLEAN DEFAULT FALSE COMMENT 'same company at both banks: counts as shared UBO + shared director',
  shared_ubo_count    NUMBER DEFAULT 0,
  shared_director_count NUMBER DEFAULT 0,
  corp_ownership_link BOOLEAN DEFAULT FALSE COMMENT 'borrower owns / is owned by counterparty (<= max hops)',
  shared_address_count NUMBER DEFAULT 0,
  shared_phone_count  NUMBER DEFAULT 0,
  timing_overlap_days NUMBER COMMENT 'NULL when counterparty unresolved',
  recorded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
  PRIMARY KEY (request_id)
);
GRANT SELECT, INSERT, UPDATE ON TABLE INVESTIGATION_FACTS TO ROLE SURAKSHA_APP;
GRANT SELECT ON TABLE RULE_WEIGHTS TO ROLE SURAKSHA_APP;
GRANT SELECT ON TABLE RULE_PARAMS  TO ROLE SURAKSHA_APP;

-- ---- fired rules (evidence) : one row per (request, rule) that fired
CREATE OR REPLACE VIEW V_RULE_EVIDENCE AS
WITH fired AS (
  SELECT f.request_id, 'R_EXACT_HASH' AS rule_id
    FROM INVESTIGATION_FACTS f WHERE f.match_type = 'EXACT'
  UNION ALL
  SELECT f.request_id, 'R_FUZZY_MATCH'
    FROM INVESTIGATION_FACTS f WHERE f.match_type = 'FUZZY'
  UNION ALL
  SELECT f.request_id, 'R_SHARED_UBO'
    FROM INVESTIGATION_FACTS f WHERE f.same_borrower OR f.shared_ubo_count > 0
  UNION ALL
  SELECT f.request_id, 'R_SHARED_DIRECTOR'
    FROM INVESTIGATION_FACTS f WHERE f.same_borrower OR f.shared_director_count > 0
  UNION ALL
  SELECT f.request_id, 'R_CORP_OWNERSHIP'
    FROM INVESTIGATION_FACTS f WHERE f.corp_ownership_link
  UNION ALL
  SELECT f.request_id, 'R_SAME_ADDRESS'
    FROM INVESTIGATION_FACTS f WHERE f.shared_address_count > 0
  UNION ALL
  SELECT f.request_id, 'R_SAME_PHONE'
    FROM INVESTIGATION_FACTS f WHERE f.shared_phone_count > 0
  UNION ALL
  SELECT f.request_id, 'R_TIMING_OVERLAP'
    FROM INVESTIGATION_FACTS f
    WHERE f.timing_overlap_days IS NOT NULL
      AND f.timing_overlap_days <= (SELECT value FROM RULE_PARAMS WHERE param = 'timing_window_days')
)
SELECT fired.request_id, fired.rule_id, w.weight, w.description
FROM fired JOIN RULE_WEIGHTS w ON w.rule_id = fired.rule_id;

-- ---- score + band per request (score = sum of weights capped at 1; HIGH if >= threshold)
CREATE OR REPLACE VIEW V_CONFIDENCE AS
SELECT
  f.request_id,
  LEAST(1, COALESCE(SUM(e.weight), 0))                               AS score,
  IFF(LEAST(1, COALESCE(SUM(e.weight), 0))
        >= (SELECT value FROM RULE_PARAMS WHERE param = 'confidence_threshold'),
      'HIGH', 'LOW')                                                 AS band,
  ARRAY_AGG(e.rule_id) WITHIN GROUP (ORDER BY e.rule_id)             AS fired_rules
FROM INVESTIGATION_FACTS f
LEFT JOIN V_RULE_EVIDENCE e ON e.request_id = f.request_id
GROUP BY f.request_id;

GRANT SELECT ON VIEW V_RULE_EVIDENCE TO ROLE SURAKSHA_APP;
GRANT SELECT ON VIEW V_CONFIDENCE    TO ROLE SURAKSHA_APP;

-- Notes on agreement with Python:
--  * SQL uses exact NUMBER(5,4) decimals; Python sums floats (0.1+0.2+0.3 != 0.6 exactly).
--    agents/confidence.py should round(score, 4) before comparing to the threshold.
--  * Citations (document/registry refs) live only in the Python Evidence objects; SQL returns rule ids.
