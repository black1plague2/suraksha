# Suraksha Streamlit app

Four persona pages: Trade-ops analyst (intake queue), Investigator (plain-English linkage Q&A), Compliance officer / MLRO (STR review, approve/reject, audit chain verify), Risk head (KPIs, exposure, daily summary).

    pip install -e ".[app]"   # or: pip install streamlit pandas
    streamlit run app/streamlit_app.py

Backend (auto-detected, shown in the sidebar):
- Inside Streamlit-in-Snowflake (active Snowpark session) -> **snowflake**: the app builds a `SnowflakeStore` over the session's connection and reads what `CALL SURAKSHA.CORE.LOAD_SYNTH(42)` / `CALL SURAKSHA.CORE.RUN_PIPELINE(42)` wrote (`CORE.PIPELINE_RESULTS` joined with the bank REQUESTS tables, `CASES`, `REPORTS`, `AUDIT_LOG`, REGISTRY). It never re-runs the pipeline; if `PIPELINE_RESULTS` is empty it asks you to run `RUN_PIPELINE(42)`. Approve/Reject call `CALL SURAKSHA.CORE.DECIDE_CASE(case_id, decision, officer, reason)` and show its error verbatim. "Verify audit chain" recomputes the hash chain in Python and also reports `COUNT(*)` of breaks in `CORE.V_AUDIT_VERIFY`.
- `SURAKSHA_BACKEND=snowflake` locally -> same, over a connector connection from `SNOWFLAKE_*` env vars.
- Otherwise `SURAKSHA_BACKEND=memory` (default): synthetic set (seed 42) is generated into a MemoryStore and run through `Suraksha.process` once per server (`st.cache_resource`). Decisions persist for the life of the server process.

Imports: streamlit, pandas, stdlib and suraksha; `snowflake.*` only lazily in Snowflake mode.
