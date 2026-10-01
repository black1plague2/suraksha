# Snowflake deployment

Everything lives in database `SURAKSHA`. Scripts are idempotent; re-running is safe.

## Prerequisites
- A Snowflake account (trial is fine) in a region where Cortex AISQL and your chosen model are available. If not, as ACCOUNTADMIN: `ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';`
- A user with ACCOUNTADMIN (or CREATE ROLE, CREATE WAREHOUSE, CREATE DATABASE, CREATE SHARE).
- `pip install -e ".[snowflake]"` (adds `snowflake-connector-python`; add `cryptography` for key-pair auth) and optionally the Snowflake CLI (`snow`).

## Connection
Either a named connection in `~/.snowflake/connections.toml` (`SNOWFLAKE_CONNECTION_NAME=suraksha`) or env vars:

| Var | Meaning |
|---|---|
| `SNOWFLAKE_ACCOUNT` | `<org>-<account>` |
| `SNOWFLAKE_USER` | login user |
| `SNOWFLAKE_PASSWORD` or `SNOWFLAKE_PRIVATE_KEY_PATH` (+ `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`) | auth |
| `SNOWFLAKE_ROLE` | default `SURAKSHA_APP` |
| `SNOWFLAKE_WAREHOUSE` | default `SURAKSHA_WH` |
| `SNOWFLAKE_DATABASE` | default `SURAKSHA` |
| `SURAKSHA_BACKEND=snowflake` | tells the app to use `SnowflakeStore` |

## Ordered steps
Run each file with the Snowflake CLI (`snow sql -f <file> [-c suraksha]`), or paste the same prompt into CoCo, e.g. *"Run sql/00_setup.sql against my account and report any errors"*.

| # | File | Run as | What it does |
|---|---|---|---|
| 0 | `sql/00_setup.sql` | ACCOUNTADMIN | XS warehouse (auto-suspend 60s), DB, 6 schemas, 6 roles, base grants |
| 1 | `sql/01_tables.sql` | SURAKSHA_ADMIN | registry, workflow, per-bank, consortium ledger tables; app grants |
| 2 | `sql/02_consortium_share.sql` | SURAKSHA_ADMIN then ACCOUNTADMIN (script switches) | secure view, `SP_PLEDGE_BANK_A/B/C`, `CREATE SHARE` |
| 3 | `sql/03_audit_immutability.sql` | SURAKSHA_ADMIN | `AUDIT_LOG` (INSERT+SELECT only for the app), `V_AUDIT_VERIFY[_SUMMARY]` |
| 4 | `sql/04_rules.sql` | SURAKSHA_ADMIN | weights/params tables, `INVESTIGATION_FACTS`, `V_RULE_EVIDENCE`, `V_CONFIDENCE` |
| 5 | `sql/05_cortex.sql` | SURAKSHA_ADMIN | `@CORE.DOCS` stage, `V_DOC_EXTRACTION` (AI_EXTRACT), `V_STR_NARRATIVE_PROMPT` (AI_COMPLETE) |
| 6 | `python scripts/load_synth_to_snowflake.py --truncate` | SURAKSHA_APP | loads synthetic registry/policy/transactions via `executemany` |

Then grant roles to your user: `GRANT ROLE SURAKSHA_ADMIN TO USER <u>; GRANT ROLE SURAKSHA_APP TO USER <u>;`

Upload sample documents: `snow stage copy ./samples/*.pdf @SURAKSHA.CORE.DOCS --overwrite`, then `ALTER STAGE SURAKSHA.CORE.DOCS REFRESH;`

## Python usage
```python
from suraksha.store.snowflake import SnowflakeStore, connect_from_env
from suraksha.integrations.cortex import cortex_extract, cortex_complete
conn = connect_from_env()
store = SnowflakeStore(conn)
fields = cortex_extract(conn, "@SURAKSHA.CORE.DOCS/bl_001.pdf")
```

## Verify
```sql
USE ROLE SURAKSHA_APP; USE WAREHOUSE SURAKSHA_WH;
SELECT COUNT(*) FROM SURAKSHA.REGISTRY.COMPANIES;                 -- > 0 after loader
SELECT * FROM SURAKSHA.CONSORTIUM.V_SHARED_LEDGER LIMIT 5;        -- hashes only, no names
CALL SURAKSHA.CONSORTIUM.SP_PLEDGE_BANK_A('E-TEST', '{"exact":"h"}', 1, 'tok', CURRENT_TIMESTAMP()::TIMESTAMP_NTZ);
DELETE FROM SURAKSHA.CORE.AUDIT_LOG;                              -- must fail: insufficient privileges
INSERT INTO SURAKSHA.CONSORTIUM.LEDGER (entry_id, bank_id, keys, borrower_token, pledged_at)
  SELECT 'x','BANK_A',PARSE_JSON('{}'),'t',CURRENT_TIMESTAMP()::TIMESTAMP_NTZ;   -- must fail
SELECT * FROM SURAKSHA.CORE.V_AUDIT_VERIFY_SUMMARY;               -- chain_intact = TRUE
SELECT * FROM SURAKSHA.CORE.V_CONFIDENCE;                         -- score/band per request
SHOW SHARES LIKE 'SURAKSHA_CONSORTIUM_SHARE';
SELECT * FROM SURAKSHA.CORE.V_DOC_EXTRACTION;                     -- after uploading docs
SELECT AI_COMPLETE('claude-sonnet-4-5', 'Say OK');
```
Remember: `SQL rules` (04) must be kept in sync with `config.RULE_WEIGHTS`. Round Python scores to 4 dp before comparing with the threshold.

## Cost notes
- One XS warehouse, `AUTO_SUSPEND = 60`, `INITIALLY_SUSPENDED`; a demo session costs well under 1 credit.
- Cortex charges per token/page: AI_EXTRACT per page processed, AI_COMPLETE per token. Keep demo docs to a handful of 1-page PDFs and cap narratives (prompt asks for max 180 words).
- Suspend explicitly when done: `ALTER WAREHOUSE SURAKSHA_WH SUSPEND;`. Drop everything: `DROP DATABASE SURAKSHA; DROP SHARE SURAKSHA_CONSORTIUM_SHARE; DROP WAREHOUSE SURAKSHA_WH;`

## Known caveats
- Nothing here has been executed against a live account yet (see `docs/agent-logs/snowflake-infra.md`).
- The audit verify view checks chain linkage and sequence continuity, not hash content; content re-verification is `AuditLog.verify()` in Python.
- Multi-bank is simulated in one account; the share section documents how a second account would consume it.
