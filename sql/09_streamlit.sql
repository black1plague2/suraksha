-- ============================================================================
-- Suraksha 09_streamlit.sql : Streamlit in Snowflake app SURAKSHA.CORE.SURAKSHA_APP
--
-- Target: WAREHOUSE runtime, set explicitly with RUNTIME_NAME = 'SYSTEM$WAREHOUSE_RUNTIME'
-- (docs.snowflake.com/en/sql-reference/sql/create-streamlit) because this account defaults to the
-- container runtime, and container runtime installs packages from PyPI, which needs an
-- EXTERNAL ACCESS INTEGRATION. Live run (ITER-04) failed with:
--   "SQL compilation error: External access is not supported for trial accounts."
-- Warehouse runtime takes dependencies from environment.yml (Snowflake Anaconda channel) —
-- no external network access needed.
--
-- Source: the git repo clone. MAIN_FILE is the repo-root shim `streamlit_app.py`, which adds src/ to
-- sys.path and runs app/streamlit_app.py. The app detects the Streamlit-in-Snowflake session and reads
-- the live tables written by LOAD_SYNTH / RUN_PIPELINE.
-- ============================================================================

USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

CREATE OR REPLACE STREAMLIT SURAKSHA.CORE.SURAKSHA_APP
  FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = SURAKSHA_WH
  RUNTIME_NAME = 'SYSTEM$WAREHOUSE_RUNTIME'
  COMMENT = 'Suraksha duplicate-financing detector (synthetic data)';

GRANT USAGE ON STREAMLIT SURAKSHA.CORE.SURAKSHA_APP TO ROLE SURAKSHA_APP;

-- ---------------------------------------------------------------------------
-- ALTERNATIVE (paid accounts only): container runtime + PyPI egress.
-- USE ROLE ACCOUNTADMIN;
-- CREATE OR REPLACE NETWORK RULE SURAKSHA.CORE.PYPI_RULE
--   MODE = EGRESS TYPE = HOST_PORT VALUE_LIST = ('pypi.org:443', 'files.pythonhosted.org:443');
-- CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION SURAKSHA_PYPI_EAI
--   ALLOWED_NETWORK_RULES = (SURAKSHA.CORE.PYPI_RULE) ENABLED = TRUE;
-- GRANT USAGE ON INTEGRATION SURAKSHA_PYPI_EAI TO ROLE SURAKSHA_ADMIN;
-- GRANT USAGE ON COMPUTE POOL SYSTEM_COMPUTE_POOL_CPU TO ROLE SURAKSHA_ADMIN;
-- USE ROLE SURAKSHA_ADMIN;
-- CREATE OR REPLACE STREAMLIT SURAKSHA.CORE.SURAKSHA_APP
--   FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/
--   MAIN_FILE = 'streamlit_app.py' QUERY_WAREHOUSE = SURAKSHA_WH
--   RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11' COMPUTE_POOL = SYSTEM_COMPUTE_POOL_CPU
--   EXTERNAL_ACCESS_INTEGRATIONS = (SURAKSHA_PYPI_EAI);
-- (container runtime reads requirements.txt / pyproject.toml, not environment.yml)
-- ---------------------------------------------------------------------------

-- Open: Snowsight > Projects > Streamlit > SURAKSHA_APP.
-- After new commits: ALTER GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO FETCH; then re-run this file.
