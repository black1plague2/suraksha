# batch-mode (ITER-05) log

## Built
- `src/suraksha/store/batch.py`: `BatchSnowflakeRun` (read -> screen -> flush), `run_batch`, `verify_continuation`, row builders for PIPELINE_RESULTS / INVESTIGATION_FACTS (same columns as sql/08 `record()`).
- `store/snowflake.py` (additive): `batch_select_insert`, `all_transactions`, `bulk_exec`, `bulk_insert_{requests,fields,ledger,reports,cases,audit}`; row-tuple builders and INSERT SQL now shared between single-row and bulk paths (single-row behaviour unchanged, existing tests green).
- `sql/11_run_pipeline_batch.sql` (`RUN_PIPELINE_BATCH(SEED, RESET)`, owner's rights, admin-only), appended to the 06 deploy chain.
- Tests: `tests/unit/test_batch_store.py` (6), static checks for 11 in `test_sql_files.py`.

## Design decisions
- Screening runs on a MemoryStore seeded from bulk reads, so results equal the plain in-memory pipeline (tested on statuses + scores, seed 42).
- Audit continuity: the last stored AuditRecord is pre-loaded into the MemoryStore audit; only records with seq > tail are flushed; `verify_continuation` checks the hash chain from the stored tail.
- Re-run guard: if any `bank:request_id` already exists in the ledger, `AlreadyProcessedError` (reset or use a fresh DB). No MERGE anywhere; fresh INSERTs only.
- Ledger written directly by the owner's-rights proc (trusted batch harness); per-bank SP_PLEDGE procs unchanged for live intake.
- Docs check (docs.snowflake.com owner's-rights restrictions): no USE inside, fully-qualified names, no caller session state; LIST is only barred in JavaScript/Scripting handlers.

## Result
- Seed 42 with a fake qmark connection: 27 statements (11 reads, 16 writes), no `%s`, no 'None', no None params. 351 tests pass; eval 5/5 goals PASS.

## Needs live verification
LIST/`session.file.get`/`session.connection` under EXECUTE AS OWNER; bind-count and statement-size limits of the large UNION ALL INSERTs (lower `chunk` if rejected); PARSE_JSON/TO_TIMESTAMP_NTZ(NULL) inside UNION ALL; DELETE on AUDIT_LOG/LEDGER by the owner; timing split read/screen/write.
