# snowflake-native log (ITER-03)

## Built
- `sql/06_git_repo.sql` (bootstrap DB/schema, CREATE GIT REPOSITORY, FETCH, LS, ordered EXECUTE IMMEDIATE FROM 00..05, 07..09, READ grants)
- `sql/07_load_synth_proc.sql` LOAD_SYNTH(seed), `sql/08_run_pipeline_proc.sql` RUN_PIPELINE(seed) (+ PIPELINE_RESULTS table, fills INVESTIGATION_FACTS so V_CONFIDENCE can be compared with Python), `sql/09_streamlit.sql`
- New root files: `streamlit_app.py` (shim) and `environment.yml` (not `app/environment.yml`; see below)
- `docs/SNOWSIGHT_RUNBOOK.md`, `tests/unit/test_sql_files.py` (29 tests)

## Confirmed against docs.snowflake.com
- CREATE GIT REPOSITORY syntax (ORIGIN, API_INTEGRATION, GIT_CREDENTIALS)
- EXECUTE IMMEDIATE FROM @repo/branches/main/... (git paths; 10MB, depth 5)
- Procedure IMPORTS of ONE .py file from a git repo stage
- CREATE STREAMLIT ... FROM @repo/branches/<b>/ ; warehouse runtime: MAIN_FILE bare filename + environment.yml

## Decisions
- Docs show no directory import, and flat file imports would collide (several `__init__.py`), so the handler fetches `src/suraksha/**.py` at runtime (COPY FILES into internal stage @CORE.CODE, then session.file.get to /tmp, sys.path). Directory IMPORTS is documented as a commented alternative.
- Streamlit warehouse runtime cannot take `app/streamlit_app.py` as MAIN_FILE, so a root shim runs the unchanged app after adding `src` to sys.path. The app's own sys.path code would fail with only app/ copied; FROM the repo root avoids that. environment.yml is at root (next to MAIN_FILE) rather than app/.
- LOAD_SYNTH runs as SURAKSHA_ADMIN (TRUNCATE), RUN_PIPELINE as SURAKSHA_APP (exercises insert-only audit); both EXECUTE AS CALLER.
- Requests are not bulk-loaded; the pipeline saves them (same as the local loader script).
- RUN_PIPELINE is single-shot per fresh DB (ledger/audit append-only).

## Unverified live (all of it)
See "Known unknowns" in docs/SNOWSIGHT_RUNBOOK.md: COPY FILES from git stage, file.get in procs, session.connection, GRANT READ ON GIT REPOSITORY, Streamlit FROM copying the whole tree, repo default branch name (local branch is `master`; paths assume `main`), secret `github_pat` schema.

## Observed
- `tests/adversarial_v2` (other agent) had 3 failing tests during my run; unrelated to these files.
