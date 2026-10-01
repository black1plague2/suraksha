-- ============================================================================
-- Suraksha 06_git_repo.sql : attach the GitHub repo to Snowflake and deploy
-- everything (00..05, 07..10) straight from Git.  Idempotent.  Browser-only flow.
--
-- RUN THIS FILE FIRST, in a Snowsight SQL worksheet, as ACCOUNTADMIN.
-- Account: <ORG-ACCOUNT> (Enterprise edition), warehouse COMPUTE_WH.
-- Pre-existing (created by the user earlier):
--     SECRET           github_pat   (TYPE = PASSWORD, PAT as password; never put the PAT in this file)
--   Section A re-creates the API INTEGRATION github_api so this file is self-contained.
--   PUBLIC REPO? Then no secret is needed: remove ONLY the two lines marked "-- PRIVATE-REPO ONLY"
--   (ALLOWED_AUTHENTICATION_SECRETS in the integration and GIT_CREDENTIALS in the repo).
--   The secret is a schema-level object.  This file assumes it lives in SURAKSHA.CORE
--   (current schema, section A).  If it lives elsewhere, fully qualify GIT_CREDENTIALS below.
--
-- ORDERING PROBLEM: the git repository object is a schema object, but 00_setup.sql is what
-- normally creates the database.  So section A creates SURAKSHA + SURAKSHA.CORE first
-- (CREATE ... IF NOT EXISTS, identical to 00, so 00 stays a no-op for them).  00 then
-- transfers ownership of the DB/schemas to SURAKSHA_ADMIN; the repo object stays owned by
-- ACCOUNTADMIN and section C grants it READ to SURAKSHA_ADMIN / SURAKSHA_APP.
--
-- BRANCH: paths below use branches/main.  Check with
--     SHOW GIT BRANCHES IN SURAKSHA.CORE.SURAKSHA_REPO;
-- and replace "main" in sql/06..09 if your default branch is different (e.g. master).
-- ============================================================================

-- ---- A. bootstrap: warehouse for this session, API integration, DB + schema for the repo object
USE ROLE ACCOUNTADMIN;
USE WAREHOUSE COMPUTE_WH;

-- Optional, only if a Cortex model is unavailable in us-east-2 (needed later for sql/05 AI functions):
-- ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';

CREATE DATABASE IF NOT EXISTS SURAKSHA COMMENT = 'Suraksha trade-finance duplicate-financing detection';
CREATE SCHEMA   IF NOT EXISTS SURAKSHA.CORE COMMENT = 'workflow state, audit log, policy, rules, docs stage';
USE DATABASE SURAKSHA;
USE SCHEMA SURAKSHA.CORE;

-- (the secret github_pat is expected in SURAKSHA.CORE; if it is elsewhere qualify both references below)
CREATE OR REPLACE API INTEGRATION github_api
  API_PROVIDER = git_https_api
  API_ALLOWED_PREFIXES = ('https://github.com/black1plague2')
  ALLOWED_AUTHENTICATION_SECRETS = (SURAKSHA.CORE.github_pat)   -- PRIVATE-REPO ONLY
  ENABLED = TRUE;

-- ---- B. the repo clone (OR REPLACE keeps it idempotent; FETCH pulls latest commits)
CREATE OR REPLACE GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO
  API_INTEGRATION = github_api
  GIT_CREDENTIALS = github_pat   -- PRIVATE-REPO ONLY: the only line to remove if the repo is public
  ORIGIN = 'https://github.com/black1plague2/suraksha.git';

ALTER GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO FETCH;

-- sanity checks (evidence: screenshot LS output; it must list sql/, src/, app/)
SHOW GIT BRANCHES IN SURAKSHA.CORE.SURAKSHA_REPO;
LS @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/;
LS @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/;

-- ---- C. deploy in order, straight from Git
-- Any warehouse works for the very first statement; 00 creates SURAKSHA_WH.
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/00_setup.sql;

-- 00 created the roles.  Let them read the repo (needed by 07, 09 and Streamlit).
USE ROLE ACCOUNTADMIN;
GRANT READ ON GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO TO ROLE SURAKSHA_ADMIN;
GRANT READ ON GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO TO ROLE SURAKSHA_APP;
USE WAREHOUSE SURAKSHA_WH;

EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/01_tables.sql;
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/02_consortium_share.sql;
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/03_audit_immutability.sql;
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/04_rules.sql;
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/05_cortex.sql;
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/07_load_synth_proc.sql;
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/08_run_pipeline_proc.sql;
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/09_streamlit.sql;
EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/10_decide_case.sql;

-- ---- D. refresh later (after pushing new commits): run ONLY the FETCH, then re-run
--         whichever sql/NN file changed (each is idempotent).
-- ALTER GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO FETCH;
-- EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/07_load_synth_proc.sql;
--
-- Notes (verify live): EXECUTE IMMEDIATE FROM with a git-repo path is documented (max file size
-- 10MB, nesting depth 5).  READ is the assumed privilege on a git repository for the roles above.
-- If a file fails midway, the whole EXECUTE fails with that statement's error; fix the file,
-- push, FETCH, and re-run it.
