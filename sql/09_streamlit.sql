-- ============================================================================
-- Suraksha 09_streamlit.sql : Streamlit in Snowflake app SURAKSHA.CORE.SURAKSHA_APP
-- Target: CONTAINER runtime (the account default; DEFAULT_STREAMLIT_COMPUTE_POOL = SYSTEM_COMPUTE_POOL_CPU).
-- Docs (CREATE STREAMLIT, container runtime): RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11'
-- (Python 3.11 is the only container version), COMPUTE_POOL, QUERY_WAREHOUSE; MAIN_FILE may be a
-- path in a subdirectory; dependencies come from pyproject.toml or requirements.txt in the source
-- tree (NOT environment.yml, which is warehouse-runtime only); packages are pulled from PyPI, which
-- per the docs requires an EXTERNAL ACCESS INTEGRATION on the app (created in section A below).
-- Source: the git repo clone (FROM copies the branch tree; verify live that src/ and app/ come along).
-- The app uses the in-process 'memory' backend; it does not read the Snowflake tables yet.
-- ============================================================================

-- ---- A. account-level pieces (ACCOUNTADMIN): PyPI egress + compute pool usage
USE ROLE ACCOUNTADMIN;
USE SCHEMA SURAKSHA.CORE;

CREATE OR REPLACE NETWORK RULE SURAKSHA.CORE.PYPI_RULE
  MODE = EGRESS TYPE = HOST_PORT
  VALUE_LIST = ('pypi.org:443', 'files.pythonhosted.org:443');

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION SURAKSHA_PYPI_EAI
  ALLOWED_NETWORK_RULES = (SURAKSHA.CORE.PYPI_RULE)
  ENABLED = TRUE;

GRANT USAGE ON INTEGRATION SURAKSHA_PYPI_EAI TO ROLE SURAKSHA_ADMIN;
GRANT USAGE ON COMPUTE POOL SYSTEM_COMPUTE_POOL_CPU TO ROLE SURAKSHA_ADMIN;  -- verify live (may already be allowed)

-- ---- B. the app (SURAKSHA_ADMIN)
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

CREATE OR REPLACE STREAMLIT SURAKSHA.CORE.SURAKSHA_APP
  FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = SURAKSHA_WH
  RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11'
  COMPUTE_POOL = SYSTEM_COMPUTE_POOL_CPU
  EXTERNAL_ACCESS_INTEGRATIONS = (SURAKSHA_PYPI_EAI)
  COMMENT = 'Suraksha duplicate-financing detector (synthetic data)';

GRANT USAGE ON STREAMLIT SURAKSHA.CORE.SURAKSHA_APP TO ROLE SURAKSHA_APP;

-- FALLBACK: warehouse runtime (MAIN_FILE must be a bare filename; dependencies from environment.yml).
-- CREATE OR REPLACE STREAMLIT SURAKSHA.CORE.SURAKSHA_APP
--   FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/
--   MAIN_FILE = 'streamlit_app.py'
--   QUERY_WAREHOUSE = SURAKSHA_WH
--   COMMENT = 'Suraksha duplicate-financing detector (synthetic data)';

-- Open: Snowsight > Projects > Streamlit > SURAKSHA_APP.
-- After new commits: ALTER GIT REPOSITORY ... FETCH; then re-run this file.
