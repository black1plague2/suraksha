# ITER-04 — Live Snowflake via CoCo: PLAN + BUILD (2026-10-01)

## CoCo (Cortex Code, Snowsight online)
- **PLAN:** CoCo reviewed PRD + `sql/` against the live Enterprise account → `docs/evidence/PLAN_coco_review.md`.
  Verified live: claude-sonnet-4-5 available, AI_EXTRACT/AI_COMPLETE work, cross-region on, git/COPY FILES GA.
  Findings: container Streamlit runtime default (HIGH), proc package loading (MEDIUM), app not reading tables, LOAD_SYNTH grant, G4 only in Python.
- **BUILD part 1:** `sql/00–05` executed from the git repo — **all succeeded first time** → `docs/evidence/BUILD_coco_00-05.md`.

## Fixes from CoCo's review (parallel agents + master)
| Who | Change |
|---|---|
| snowflake-infra r2 (Sonnet) | `09` → container runtime (`SYSTEM$ST_CONTAINER_RUNTIME_PY3_11`, `SYSTEM_COMPUTE_POOL_CPU`, PyPI external access integration); `07` LOAD_SYNTH admin-only; CASES: app role SELECT+INSERT only; **new `sql/10_decide_case.sql` DECIDE_CASE** proc enforcing G4 in SQL (named officer, not `system:*`, reject needs reason, pending only, hash-chained audit rows); runbook TEST step proving G4 in SQL |
| streamlit-app r2 (Sonnet) | app auto-detects Streamlit-in-Snowflake → SnowflakeStore over the session; reads PIPELINE_RESULTS/CASES/REPORTS/AUDIT_LOG/REGISTRY; approve/reject via `CALL DECIDE_CASE`; audit verify in Python + `V_AUDIT_VERIFY` |
| master | `approval._canon_at`: audit hash uses timezone-neutral naive-UTC timestamps (Snowflake TIMESTAMP_NTZ round-trip no longer breaks verification; matches DECIDE_CASE); `SnowflakeStore.save_case` insert-only (MERGE would need the revoked UPDATE); root `requirements.txt` for container runtime |

## Results
- `python -m pytest tests -q` → **336 passed**; eval: 5/5 goals PASS; aware/naive canonical timestamp parity ✔.

## Still "verify live" (BUILD part 2 will tell)
COPY FILES + `session.file.get` package loading in procs · Snowpark connection shim (`session.connection` / `_conn._conn`) ·
DECIDE_CASE transaction + `?` binds in owner's-rights proc · container runtime PyPI install · GRANT READ ON GIT REPOSITORY.
