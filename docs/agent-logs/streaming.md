# streaming (ITER-06) log

## Built
- `src/suraksha/store/incremental.py`: `screen_requests` (one read -> real `Suraksha` over only the new requests -> bulk flush, via `BatchSnowflakeRun`; per-request guards: unknown bank/borrower, duplicate id in batch, already-pledged; batch failure falls back to one-by-one so a poison request cannot block others), `run_inbox` (consume stream -> fetch NEW -> screen -> one bulk UPDATE), `mark_inbox`, request JSON (de)serialisation, `demo_duplicate_request` / `demo_clean_request`.
- `sql/12_streaming.sql`: `REQUEST_INBOX`, APPEND_ONLY stream, `INBOX_CONSUMED`, `SUBMIT_REQUEST` (SQL, owner), `SCREEN_INBOX` (Python 3.12, owner, shim + package check incl. incremental.py), `SCREEN_INBOX_TASK` (1 MINUTE, WHEN SYSTEM$STREAM_HAS_DATA, RESUME, commented SUSPEND), `SUBMIT_DEMO` + `SUBMIT_DEMO_DUPLICATE/CLEAN`. Appended to the 06 deploy chain (+ test_sql_files order list).
- `tests/unit/test_incremental.py` (11): parity with the plain pipeline for duplicate/clean/weak, audit chain continuation, statement budget (<40) and clean SQL, guards, JSON round trip, inbox end-to-end, sql/12 static checks.

## Design decisions
- Inbox `status` is the source of truth, not the stream: the stream (APPEND_ONLY, so status UPDATEs never retrigger) only wakes the task and its offset is advanced by `INSERT INTO INBOX_CONSUMED SELECT .. FROM stream`.
- Reuses `BatchSnowflakeRun` public read/engine/flush; no batch.py change needed. Private `SnowflakeStore._query/_exec` used for the 3 inbox statements.
- Scenario column of PIPELINE_RESULTS = `LIVE`, label_duplicate FALSE (unknown for live requests).
- Demo clean request re-files an existing clean request's documents at the SAME bank (screens CLEAR): a brand-new cargo fires R_NO_VESSEL_CALL (ITER-06 physical-cargo rule) because the port-call feed has no row for it.

## Result
- 3 new requests against the seed-42 consortium: ~24 statements (fake qmark conn), no `%s`, no 'None', no MERGE.

## Needs live verification
Stream/task syntax and `GRANT EXECUTE TASK ON ACCOUNT`; stream consume inside an owner's-rights Python proc; `OBJECT_INSERT(:REQUEST::OBJECT,..)`; nested CALL in SUBMIT_DEMO and PARSE_JSON(?) bind; TASK_HISTORY showing runs; the stage copy must contain suraksha/store/incremental.py and synth/generator.py (REQUIRED_MODULES).
