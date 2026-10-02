# Suraksha Streamlit app

Five pages (four personas + a policy what-if): Trade-ops analyst (intake queue), Investigator (plain-English linkage Q&A), Compliance officer / MLRO (STR review, approve/reject, audit chain verify), Risk head (KPIs, exposure, daily summary).

    pip install -e ".[app]"   # or: pip install streamlit pandas
    streamlit run app/streamlit_app.py

Backend (auto-detected, shown in the sidebar):
- Inside Streamlit-in-Snowflake (active Snowpark session) -> **snowflake**: the app builds a `SnowflakeStore` over the session's connection and reads what `CALL SURAKSHA.CORE.LOAD_SYNTH(42)` / `CALL SURAKSHA.CORE.RUN_PIPELINE(42)` wrote (`CORE.PIPELINE_RESULTS` joined with the bank REQUESTS tables, `CASES`, `REPORTS`, `AUDIT_LOG`, REGISTRY). It never re-runs the pipeline; if `PIPELINE_RESULTS` is empty it asks you to run `RUN_PIPELINE(42)`. Approve/Reject call `CALL SURAKSHA.CORE.DECIDE_CASE(case_id, decision, officer, reason)` and show its error verbatim. "Verify audit chain" recomputes the hash chain in Python and also reports `COUNT(*)` of breaks in `CORE.V_AUDIT_VERIFY`.
- `SURAKSHA_BACKEND=snowflake` locally -> same, over a connector connection from `SNOWFLAKE_*` env vars.
- Otherwise `SURAKSHA_BACKEND=memory` (default): synthetic set (seed 42) is generated into a MemoryStore and run through `Suraksha.process` once per server (`st.cache_resource`). Decisions persist for the life of the server process.

Imports: streamlit, pandas, stdlib and suraksha; `snowflake.*` only lazily in Snowflake mode.

New in round 3:
- **Policy what-if** (Risk head / MLRO): sliders for the confidence threshold and every rule weight (rule list read dynamically from `config.RULE_WEIGHTS`, plus any extra rule ids seen in the results). Scores are recomputed vectorised from each request's fired rules (memory: `PipelineResult.confidence.evidence` + `SynthDataset.labels`; Snowflake: `PIPELINE_RESULTS.py_rules` + `label_duplicate`). Shows detection rate, FPR, confusion matrix, delta vs current policy, requests that flip band, and per-rule marginal value. Positive = HIGH band. Simulation only - changing live policy requires governed approval.
- **Evidence chain** on the MLRO page: Documents -> Consortium match (hash prefixes) -> Ownership path (graphviz) -> Rules fired (weights, sum, threshold) -> STR (jurisdiction selector from `suraksha.agents.report_templates` when present) -> Decision & audit trail. Snowflake mode shows documents, fired rules, match facts from `CORE.INVESTIGATION_FACTS` (if readable) and the STR; the ownership graph is memory-mode only.
- **Risk head**: exposure held (PENDING_APPROVAL + FILED, by currency), median request-to-drafted-finding time (audit `REQUEST_RECEIVED` -> `CASE_OPENED`), analyst queue size.
