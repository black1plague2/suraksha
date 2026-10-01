# snowflake-infra log

## Built
- `sql/00_setup.sql` .. `05_cortex.sql` (idempotent), `docs/SNOWFLAKE_DEPLOY.md`
- `src/suraksha/store/snowflake.py`: `SnowflakeStore` (all Store methods + `get_fields` + `load_registry` + `close`), `connect_from_env`
- `src/suraksha/integrations/cortex.py`: `cortex_extract(conn, stage_path, request_id=None)`, `cortex_complete(conn, prompt, model="claude-sonnet-4-5")`
- `scripts/load_synth_to_snowflake.py` (lazy-imports `suraksha.synth.generator.generate`; `--truncate`, `--connection`)
- `tests/unit/test_snowflake_store.py` (fake connector, 17 tests)

## Decisions
- Layout: REGISTRY.{COMPANIES,PERSONS,ROLES,CORP_OWNERS,ADDRESSES}; CORE.{POLICY_CLAUSES,REQUEST_FIELDS,REPORTS,CASES,AUDIT_LOG,RULE_WEIGHTS,RULE_PARAMS,INVESTIGATION_FACTS}; BANK_x.{REQUESTS,TRANSACTIONS}; CONSORTIUM.LEDGER + V_SHARED_LEDGER.
- Store reads the consortium through the secure view, writes via per-bank owner's-rights procs `SP_PLEDGE_BANK_A/B/C` with the bank id hard-coded (avoids relying on CURRENT_ROLE semantics inside owner's-rights procs). Nobody but the owner has INSERT on LEDGER.
- Bank schema names / proc names are whitelisted constants; all values are bind params; VARIANT via PARSE_JSON(%s) in INSERT...SELECT / MERGE. Datetimes are bound as ISO strings through TO_TIMESTAMP_NTZ (aware datetimes normalised to naive UTC).
- `transactions_for(company, None)` and `get_request` UNION ALL the three bank schemas.
- CORE grants are explicit per table so re-running 01 can't hand UPDATE on AUDIT_LOG to the app role.
- 04 mirrors RULE_WEIGHTS with exact NUMBER(5,4) in tables; threshold 0.6 and timing window 45 in RULE_PARAMS.

## Unverified live (no account)
- All SQL (syntax, grants, owner's-rights procs with `:param` binds, `CALL` with `TO_TIMESTAMP_NTZ(%s)` argument expression, AI_EXTRACT object literal in a view, `AI_COMPLETE` model availability, `SNOWFLAKE_SSE` stage + `TO_FILE`, CREATE SHARE of a secure view).
- AI_EXTRACT syntax confirmed against docs.snowflake.com (`file => TO_FILE(...)`, `responseFormat => {...}`, result `{response, error}`); AI_COMPLETE(model, prompt) from memory.
- Connector behaviours: executemany with INSERT...SELECT (loops, slower), VARIANT returned as JSON text (handled).

## Gaps / contract change requests
1. `V_AUDIT_VERIFY` checks linkage + seq continuity only; it cannot recompute `entry_hash` because the canonical JSON is Python-defined. If master documents an exact canonical form (e.g. `json.dumps(sort_keys=True, separators=(",",":"))` of seq/at/actor/action/subject_id/payload with fixed `at` format) a SQL SHA2 recompute can be added.
2. `agents/confidence.py` should `round(score, 4)` before threshold compare so it agrees with the SQL decimal sum.
3. `append_audit` contiguity check is read-then-insert (not atomic); fine for single-writer demo.

## pytest
`python -m pytest tests/unit/test_snowflake_store.py -q` -> `17 passed in 0.09s`
