-- ============================================================================
-- Suraksha 09_streamlit.sql : Streamlit in Snowflake app SURAKSHA.CORE.SURAKSHA_APP
-- Source: the git repo clone.  Docs: for the warehouse runtime MAIN_FILE must be a bare
-- filename (subdirectory paths are only for container runtime) and environment.yml sits next
-- to it.  So the repo root carries a tiny shim `streamlit_app.py` (adds ./src to sys.path and
-- runs app/streamlit_app.py unchanged) and a root `environment.yml`.
-- FROM copies the branch tree (verify live that src/ and app/ come along).
-- The app uses the in-process 'memory' backend (generates + screens synthetic data itself);
-- it does not read the Snowflake tables yet.  Run as SURAKSHA_ADMIN.
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

CREATE OR REPLACE STREAMLIT SURAKSHA.CORE.SURAKSHA_APP
  FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = SURAKSHA_WH
  COMMENT = 'Suraksha duplicate-financing detector (synthetic data)';

GRANT USAGE ON STREAMLIT SURAKSHA.CORE.SURAKSHA_APP TO ROLE SURAKSHA_APP;

-- Open: Snowsight > Projects > Streamlit > SURAKSHA_APP.
-- After new commits: ALTER GIT REPOSITORY ... FETCH; then re-run this file.
-- If this errors (newer accounts may default to container runtime: RUNTIME_NAME + COMPUTE_POOL,
-- where MAIN_FILE = 'app/streamlit_app.py' is allowed) -- verify live.
