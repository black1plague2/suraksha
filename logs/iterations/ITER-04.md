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

## BUILD part 2 (live)
- CoCo ran `01` (re-run), `07` LOAD_SYNTH, `08` RUN_PIPELINE → **all succeeded**.
- `09` failed: `SQL compilation error: External access is not supported for trial accounts.` (container runtime → PyPI egress).
- Fix (master): `09` pins `RUNTIME_NAME = 'SYSTEM$WAREHOUSE_RUNTIME'` (confirmed in CREATE STREAMLIT docs), dependencies from
  `environment.yml` (Snowflake Anaconda channel), no external access; container variant kept commented for paid accounts;
  removed root `requirements.txt`; test updated. 336 tests pass.
- CoCo ran `10` DECIDE_CASE → **succeeded**. `CALL LOAD_SYNTH(42)` failed: `SQL compilation error ... unexpected '%'` at
  `store/snowflake.py` `load_registry` → `executemany`. CoCo's diagnosis: Snowpark-hosted connector binds with `?` (qmark).
  This also proved the two biggest "verify live" items WORK: package loading from the git repo, and the connection shim.
- Fix (master): `SnowflakeStore` converts `%s`→`?` when the connection declares `paramstyle = "qmark"`; the three Snowpark
  shims (sql/07, sql/08, app) declare qmark and their cursors convert too (covers direct MERGEs in 08 and `CALL DECIDE_CASE`
  in the app). +2 tests; proc bodies compile. **338 passed.**
- Re-run: ADDRESSES loaded (qmark fix confirmed live), then ROLES failed: `Numeric value 'None' is not recognized` (PCT FLOAT).
  Data holds real Python None (500 roles without pct) — the Snowpark-hosted connector binds None as the string 'None'.
- Fix (master): `store.snowflake.inline_nulls(sql, params)` swaps a `?` whose value is None for the keyword NULL (values are
  still bound, never interpolated); used by all three Snowpark shims (07, 08, app); shim executemany routes through execute.
  +1 test; simulated shim output verified. **339 passed.**
- Re-run: 07/08/**09 succeeded** (Streamlit app created on warehouse runtime). `CALL LOAD_SYNTH(42)` ran but crawled —
  Query History showed one `INSERT INTO REGISTRY.ROLES` per row (~6k single-row INSERTs inside the proc).
- Fix (master): `store.snowflake.batch_values_insert` turns single-row qmark `INSERT … VALUES (?, …)` + N rows into multi-row
  INSERTs (chunk 300, None → NULL); shims' executemany use it, falling back per-row for `INSERT … SELECT`. Simulated full
  registry load: **~6,000 → 37 statements**, no None params. +1 test; **340 passed.**
- Re-run still showed per-row `INSERT INTO REGISTRY.COMPANIES` → the OLD proc body ran (new shim would batch, or fail
  importing `batch_values_insert` from a stale package). Hardening (master): procs load the package from the git clone FIRST
  (current right after FETCH; `@CORE.CODE` copy only as fallback), purge any already-imported `suraksha*` modules each call
  (warm sandbox), and return `proc_version` ("iter04-batch-gitfirst") in their JSON so the running version is visible.
