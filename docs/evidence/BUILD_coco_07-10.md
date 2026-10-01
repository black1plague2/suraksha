# CoCo evidence — BUILD phase, part 2 (2026-10-01)

| Step | Live result | Fix pushed |
|---|---|---|
| `01` re-run, `07`, `08` | succeeded | — |
| `09` Streamlit (container runtime) | `External access is not supported for trial accounts.` | warehouse runtime pinned (`SYSTEM$WAREHOUSE_RUNTIME`) |
| `10` DECIDE_CASE | succeeded | — |
| `CALL LOAD_SYNTH(42)` #1 | `unexpected '%'` (Snowpark-hosted connector is qmark) | `%s`→`?` in store + shims |
| `CALL LOAD_SYNTH(42)` #2 | `Numeric value 'None' is not recognized` (ROLES.PCT) | None → literal NULL (`inline_nulls`) |
| `CALL LOAD_SYNTH(42)` #3 | ran, but ~6k single-row INSERTs | multi-row batched INSERTs (6k → 37 statements) |
| `CALL LOAD_SYNTH(42)` #4 | old proc body still running | git-first package load, purge stale modules, `proc_version` |
| `09` re-run | **succeeded** — app `SURAKSHA.CORE.SURAKSHA_APP` created | — |
| `CALL LOAD_SYNTH(42)` #5 | **succeeded**: 234 companies · 607 persons · 1087 roles · 35 corp_owners · 226 addresses · 3908 txns · 12 clauses | — |

Each failure was diagnosed by Cortex Code from the live error, fixed in the repo, pushed, then FETCHed into Snowflake.
Screenshots: _add `docs/evidence/build_load_synth.png`_
