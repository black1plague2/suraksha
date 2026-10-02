-- ============================================================================
-- Suraksha 01_tables.sql : tables mirroring src/suraksha/store/base.py shapes.
-- Run as SURAKSHA_ADMIN.  Idempotent (IF NOT EXISTS; use CREATE OR REPLACE to reset).
-- NOTE: Snowflake does not enforce PRIMARY KEY (informational only); the Python
-- store uses MERGE / explicit existence checks where uniqueness matters.
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE DATABASE SURAKSHA;

-- ------------------------------------------------------------------ REGISTRY
USE SCHEMA REGISTRY;
CREATE TABLE IF NOT EXISTS ADDRESSES (
  address_id VARCHAR NOT NULL, line VARCHAR, city VARCHAR, country VARCHAR,
  PRIMARY KEY (address_id)
);
CREATE TABLE IF NOT EXISTS COMPANIES (
  company_id VARCHAR NOT NULL, name VARCHAR, reg_no VARCHAR, address_id VARCHAR,
  phone VARCHAR, incorporated DATE, country VARCHAR,
  PRIMARY KEY (company_id)
);
CREATE TABLE IF NOT EXISTS PERSONS (
  person_id VARCHAR NOT NULL, name VARCHAR, id_hash VARCHAR,
  PRIMARY KEY (person_id)
);
CREATE TABLE IF NOT EXISTS ROLES (
  company_id VARCHAR NOT NULL, person_id VARCHAR NOT NULL,
  role VARCHAR NOT NULL COMMENT 'DIRECTOR | SHAREHOLDER | UBO', pct FLOAT
);
CREATE TABLE IF NOT EXISTS CORP_OWNERS (
  owner_company_id VARCHAR NOT NULL, owned_company_id VARCHAR NOT NULL, pct FLOAT
);
-- Port-call feed: synthetic stand-in for Snowflake Marketplace AIS / port-call data (physical-cargo rule
-- R_NO_VESSEL_CALL, sql/04).  One row per (vessel, voyage, port) call.  Loaded by LOAD_SYNTH (sql/07).
CREATE TABLE IF NOT EXISTS VESSEL_CALLS (
  vessel VARCHAR NOT NULL, voyage VARCHAR NOT NULL, port VARCHAR NOT NULL,
  arrived DATE NOT NULL, departed DATE NOT NULL
);

-- ------------------------------------------------------------------ CORE
USE SCHEMA CORE;
CREATE TABLE IF NOT EXISTS POLICY_CLAUSES (
  clause_id VARCHAR NOT NULL, section VARCHAR, title VARCHAR, text VARCHAR,
  tags ARRAY COMMENT 'array of strings',
  PRIMARY KEY (clause_id)
);
CREATE TABLE IF NOT EXISTS REQUEST_FIELDS (
  request_id VARCHAR NOT NULL, bl_number VARCHAR, vessel VARCHAR, voyage VARCHAR,
  port_of_loading VARCHAR, port_of_discharge VARCHAR, commodity VARCHAR,
  quantity FLOAT, quantity_unit VARCHAR, value FLOAT, currency VARCHAR,
  shipment_date DATE, shipper VARCHAR, consignee VARCHAR,
  sources VARIANT COMMENT 'field name -> citation object',
  saved_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
  PRIMARY KEY (request_id)
);
CREATE TABLE IF NOT EXISTS REPORTS (
  report_id VARCHAR NOT NULL, request_id VARCHAR, reporting_bank_id VARCHAR,
  created_at TIMESTAMP_NTZ, recommended_action VARCHAR,
  sections VARIANT COMMENT 'list of sections -> sentences -> citations',
  PRIMARY KEY (report_id)
);
CREATE TABLE IF NOT EXISTS CASES (
  case_id VARCHAR NOT NULL, request_id VARCHAR, report_id VARCHAR,
  status VARCHAR, hold_recommended BOOLEAN,
  decided_by VARCHAR, decided_at TIMESTAMP_NTZ, reason VARCHAR,
  PRIMARY KEY (case_id)
);

-- ------------------------------------------------------------------ BANK PRIVATE (A, B, C)
-- Each bank schema holds its own financing requests and transactions.
CREATE TABLE IF NOT EXISTS BANK_A.REQUESTS (
  request_id VARCHAR NOT NULL, bank_id VARCHAR NOT NULL, borrower_id VARCHAR,
  amount FLOAT, currency VARCHAR, submitted_at TIMESTAMP_NTZ,
  documents VARIANT COMMENT 'array of {doc_id, request_id, doc_type, text, pages}',
  PRIMARY KEY (request_id)
);
CREATE TABLE IF NOT EXISTS BANK_A.TRANSACTIONS (
  txn_id VARCHAR NOT NULL, company_id VARCHAR NOT NULL, bank_id VARCHAR NOT NULL,
  txn_date DATE, amount FLOAT, currency VARCHAR, counterparty VARCHAR, txn_type VARCHAR,
  PRIMARY KEY (txn_id)
);
CREATE TABLE IF NOT EXISTS BANK_B.REQUESTS (
  request_id VARCHAR NOT NULL, bank_id VARCHAR NOT NULL, borrower_id VARCHAR,
  amount FLOAT, currency VARCHAR, submitted_at TIMESTAMP_NTZ,
  documents VARIANT COMMENT 'array of {doc_id, request_id, doc_type, text, pages}',
  PRIMARY KEY (request_id)
);
CREATE TABLE IF NOT EXISTS BANK_B.TRANSACTIONS (
  txn_id VARCHAR NOT NULL, company_id VARCHAR NOT NULL, bank_id VARCHAR NOT NULL,
  txn_date DATE, amount FLOAT, currency VARCHAR, counterparty VARCHAR, txn_type VARCHAR,
  PRIMARY KEY (txn_id)
);
CREATE TABLE IF NOT EXISTS BANK_C.REQUESTS (
  request_id VARCHAR NOT NULL, bank_id VARCHAR NOT NULL, borrower_id VARCHAR,
  amount FLOAT, currency VARCHAR, submitted_at TIMESTAMP_NTZ,
  documents VARIANT COMMENT 'array of {doc_id, request_id, doc_type, text, pages}',
  PRIMARY KEY (request_id)
);
CREATE TABLE IF NOT EXISTS BANK_C.TRANSACTIONS (
  txn_id VARCHAR NOT NULL, company_id VARCHAR NOT NULL, bank_id VARCHAR NOT NULL,
  txn_date DATE, amount FLOAT, currency VARCHAR, counterparty VARCHAR, txn_type VARCHAR,
  PRIMARY KEY (txn_id)
);

-- ------------------------------------------------------------------ CONSORTIUM ledger
CREATE TABLE IF NOT EXISTS CONSORTIUM.LEDGER (
  entry_id VARCHAR NOT NULL,
  bank_id VARCHAR NOT NULL,
  keys VARIANT NOT NULL COMMENT 'salted hashes: {"exact":..,"cargo":..,"bl":..}',
  qty_band NUMBER(18,0),
  borrower_token VARCHAR NOT NULL COMMENT 'salted hash of reg_no, never a name',
  pledged_at TIMESTAMP_NTZ NOT NULL,
  -- internal columns, NOT exposed through the secure view / share:
  inserted_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
  inserted_by VARCHAR DEFAULT CURRENT_USER(),
  PRIMARY KEY (entry_id)
);

-- ------------------------------------------------------------------ app grants
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA SURAKSHA.REGISTRY TO ROLE SURAKSHA_APP;
-- CORE grants are explicit per table (NOT "ALL TABLES") so re-running this file can never
-- hand UPDATE/DELETE on AUDIT_LOG to the app role.
GRANT SELECT, INSERT, UPDATE ON TABLE SURAKSHA.CORE.POLICY_CLAUSES TO ROLE SURAKSHA_APP;
GRANT SELECT, INSERT, UPDATE ON TABLE SURAKSHA.CORE.REQUEST_FIELDS TO ROLE SURAKSHA_APP;  -- MERGE upsert
GRANT SELECT, INSERT, UPDATE ON TABLE SURAKSHA.CORE.REPORTS        TO ROLE SURAKSHA_APP;  -- MERGE upsert
-- CASES: app gets INSERT + SELECT only.  Decisions (status/decided_by/hold_recommended) change ONLY through
-- the owner's-rights procedure SURAKSHA.CORE.DECIDE_CASE (sql/10), so the human-approval gate G4 is enforced in SQL.
GRANT SELECT, INSERT ON TABLE SURAKSHA.CORE.CASES TO ROLE SURAKSHA_APP;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE SURAKSHA.CORE.CASES FROM ROLE SURAKSHA_APP;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA SURAKSHA.BANK_A   TO ROLE SURAKSHA_APP;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA SURAKSHA.BANK_B   TO ROLE SURAKSHA_APP;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA SURAKSHA.BANK_C   TO ROLE SURAKSHA_APP;
-- The consortium LEDGER is deliberately NOT granted to the app for writes: see 02_consortium_share.sql.
-- 03_audit_immutability.sql creates AUDIT_LOG and grants INSERT+SELECT only (no UPDATE/DELETE).
-- Tables added later need these grants re-run (or add FUTURE GRANTS).
