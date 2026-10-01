-- ============================================================================
-- Suraksha 02_consortium_share.sql : hash-only consortium ledger, secure view,
-- per-bank write procedures, and a SHARE demonstrating secure data sharing.
-- Run as SURAKSHA_ADMIN, except CREATE SHARE (needs CREATE SHARE privilege / ACCOUNTADMIN).
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE DATABASE SURAKSHA;
USE SCHEMA CONSORTIUM;

-- ---- secure view: exposes ONLY hashes + band + token + timestamp.
-- No names, no amounts, no inserted_by.  SECURE hides the definition and blocks
-- optimiser-based data leakage, which is required for views placed in a share.
CREATE OR REPLACE SECURE VIEW V_SHARED_LEDGER
  COMMENT = 'Consortium ledger as shared with member banks (hashes only)'
AS
SELECT entry_id, bank_id, keys, qty_band, borrower_token, pledged_at
FROM LEDGER;

-- ---- write path: one owner's-rights procedure per bank. The bank id is HARD-CODED in
-- each procedure and the caller cannot override it, so BANK_A_ROLE can only ever
-- insert BANK_A rows.  No role holds INSERT on LEDGER directly.
-- keys arrive as a JSON string and are parsed inside (CALL arguments are scalars).
CREATE OR REPLACE PROCEDURE SP_PLEDGE_BANK_A(P_ENTRY_ID VARCHAR, P_KEYS_JSON VARCHAR, P_QTY_BAND NUMBER, P_BORROWER_TOKEN VARCHAR, P_PLEDGED_AT TIMESTAMP_NTZ)
  RETURNS VARCHAR
  LANGUAGE SQL
  EXECUTE AS OWNER
AS
$$
BEGIN
  INSERT INTO SURAKSHA.CONSORTIUM.LEDGER (entry_id, bank_id, keys, qty_band, borrower_token, pledged_at)
    SELECT :P_ENTRY_ID, 'BANK_A', PARSE_JSON(:P_KEYS_JSON), :P_QTY_BAND, :P_BORROWER_TOKEN, :P_PLEDGED_AT;
  RETURN :P_ENTRY_ID;
END;
$$;

CREATE OR REPLACE PROCEDURE SP_PLEDGE_BANK_B(P_ENTRY_ID VARCHAR, P_KEYS_JSON VARCHAR, P_QTY_BAND NUMBER, P_BORROWER_TOKEN VARCHAR, P_PLEDGED_AT TIMESTAMP_NTZ)
  RETURNS VARCHAR
  LANGUAGE SQL
  EXECUTE AS OWNER
AS
$$
BEGIN
  INSERT INTO SURAKSHA.CONSORTIUM.LEDGER (entry_id, bank_id, keys, qty_band, borrower_token, pledged_at)
    SELECT :P_ENTRY_ID, 'BANK_B', PARSE_JSON(:P_KEYS_JSON), :P_QTY_BAND, :P_BORROWER_TOKEN, :P_PLEDGED_AT;
  RETURN :P_ENTRY_ID;
END;
$$;

CREATE OR REPLACE PROCEDURE SP_PLEDGE_BANK_C(P_ENTRY_ID VARCHAR, P_KEYS_JSON VARCHAR, P_QTY_BAND NUMBER, P_BORROWER_TOKEN VARCHAR, P_PLEDGED_AT TIMESTAMP_NTZ)
  RETURNS VARCHAR
  LANGUAGE SQL
  EXECUTE AS OWNER
AS
$$
BEGIN
  INSERT INTO SURAKSHA.CONSORTIUM.LEDGER (entry_id, bank_id, keys, qty_band, borrower_token, pledged_at)
    SELECT :P_ENTRY_ID, 'BANK_C', PARSE_JSON(:P_KEYS_JSON), :P_QTY_BAND, :P_BORROWER_TOKEN, :P_PLEDGED_AT;
  RETURN :P_ENTRY_ID;
END;
$$;

-- ---- grants: each bank role may EXECUTE only its own procedure and READ the shared view.
GRANT USAGE ON PROCEDURE SP_PLEDGE_BANK_A(VARCHAR, VARCHAR, NUMBER, VARCHAR, TIMESTAMP_NTZ) TO ROLE BANK_A_ROLE;
GRANT USAGE ON PROCEDURE SP_PLEDGE_BANK_B(VARCHAR, VARCHAR, NUMBER, VARCHAR, TIMESTAMP_NTZ) TO ROLE BANK_B_ROLE;
GRANT USAGE ON PROCEDURE SP_PLEDGE_BANK_C(VARCHAR, VARCHAR, NUMBER, VARCHAR, TIMESTAMP_NTZ) TO ROLE BANK_C_ROLE;
GRANT SELECT ON VIEW V_SHARED_LEDGER TO ROLE BANK_A_ROLE;
GRANT SELECT ON VIEW V_SHARED_LEDGER TO ROLE BANK_B_ROLE;
GRANT SELECT ON VIEW V_SHARED_LEDGER TO ROLE BANK_C_ROLE;
-- SURAKSHA_APP inherits all three bank roles (single-account simulation).
-- Direct reads of the base table stay with the owner only.

-- ---- secure data sharing -----------------------------------------------------
-- CREATE SHARE needs the CREATE SHARE account privilege (ACCOUNTADMIN by default).
USE ROLE ACCOUNTADMIN;
CREATE SHARE IF NOT EXISTS SURAKSHA_CONSORTIUM_SHARE
  COMMENT = 'Hash-only consortium ledger for member banks';
GRANT USAGE ON DATABASE SURAKSHA TO SHARE SURAKSHA_CONSORTIUM_SHARE;
GRANT USAGE ON SCHEMA SURAKSHA.CONSORTIUM TO SHARE SURAKSHA_CONSORTIUM_SHARE;
GRANT SELECT ON VIEW SURAKSHA.CONSORTIUM.V_SHARED_LEDGER TO SHARE SURAKSHA_CONSORTIUM_SHARE;
-- (Only the secure view is granted; LEDGER, bank schemas and registry are NOT in the share.)

-- How a REAL second bank account would consume it (do not run in a single-account demo):
--   -- provider side: add the consumer account
--   ALTER SHARE SURAKSHA_CONSORTIUM_SHARE ADD ACCOUNTS = <org_name>.<bank_b_account>;
--   -- consumer side (Bank B's account, as ACCOUNTADMIN):
--   CREATE DATABASE CONSORTIUM_FROM_PROVIDER FROM SHARE <org_name>.<provider_account>.SURAKSHA_CONSORTIUM_SHARE;
--   GRANT IMPORTED PRIVILEGES ON DATABASE CONSORTIUM_FROM_PROVIDER TO ROLE <bank_b_role>;
--   SELECT * FROM CONSORTIUM_FROM_PROVIDER.CONSORTIUM.V_SHARED_LEDGER;
-- Writes in a multi-account design: each bank publishes its own ledger share and a
-- consortium operator unions them; the single-account simulation here uses the
-- per-bank procedures above instead.

USE ROLE SURAKSHA_ADMIN;
