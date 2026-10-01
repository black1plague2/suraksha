# streamlit-app log
- Built app/streamlit_app.py (4 personas), app/README.md, tests/unit/test_app_smoke.py (AppTest per page).
- Full suite: 184 passed.
- Notes: st.markdown lacks footnotes, so [^n] markers are rendered as bold [n]. Status badge shows FILED/CLOSED once a case is decided.

## Round 2 (ITER-04)
- Problem: under Streamlit-in-Snowflake the app used the in-memory backend and ignored LOAD_SYNTH/RUN_PIPELINE data.
- `detect_backend()`: `snowflake.snowpark.context.get_active_session()` succeeds -> Snowflake mode; else `SURAKSHA_BACKEND` (`snowflake` = connector via env, default `memory`). Active backend shown in sidebar.
- Snowflake mode: `SnowflakeStore` over `_Conn(session)` (DB-API shim copied from sql/08). Read-only views from CORE.PIPELINE_RESULTS + bank REQUESTS + REQUEST_FIELDS + CASES/REPORTS/AUDIT_LOG/REGISTRY; empty PIPELINE_RESULTS -> "Run CALL SURAKSHA.CORE.RUN_PIPELINE(42) first". Pipeline is never re-run.
- Approve/Reject -> `CALL SURAKSHA.CORE.DECIDE_CASE(case_id, decision, officer, reason)` (sql/10); errors (or a returned `{"error": ...}`) shown verbatim. Memory mode still uses `Suraksha.decide`.
- Verify audit chain (Snowflake): `AuditLog.verify()` over `store.list_audit()` with naive TIMESTAMP_NTZ values re-tagged UTC (hash was computed over aware UTC), plus breaks count from CORE.V_AUDIT_VERIFY.
- environment.yml: added snowflake-snowpark-python. Tests: +fake-session smoke tests (detection, empty results, 4 pages, verify, decide routing, decide error). Suite: 336 passed.
- Needs live verification: DECIDE_CASE return shape (parsed as JSON; `error` key => refusal), `session.connection` vs `session._conn._conn` in SiS, TIMESTAMP_NTZ hash round-trip, SiS container vs warehouse runtime package availability.
