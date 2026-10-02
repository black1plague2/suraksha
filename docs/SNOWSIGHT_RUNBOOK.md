# Snowsight runbook (browser only, CoCo online)

Account `<ORG-ACCOUNT>` (Enterprise, AWS), role ACCOUNTADMIN, warehouse COMPUTE_WH. Nothing runs locally. The repo is attached to Snowflake as a Git repository object
(`SURAKSHA.CORE.SURAKSHA_REPO`, API integration `github_api`, secret `github_pat`), so every script is deployed with
`EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/NN_*.sql`.

Anything marked *(verify live)* was not confirmed against a live account; if it fails, paste the error to CoCo and fix.

## 0. Before you start
- Worksheet role ACCOUNTADMIN. Push the latest commit to GitHub first (Snowflake only sees what is on the remote).
- Confirm the default branch name is `main` (if it is `master`, replace `branches/main` with `branches/master` in `sql/06..09`).
- `sql/06` re-creates the API integration `github_api` itself. If the repo is public, delete the two lines marked `PRIVATE-REPO ONLY` (secret allow-list and `GIT_CREDENTIALS`).
- If a Cortex model is unavailable in us-east-2 (step 4.5 or sql/05 fails): `-- ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';` (commented in `sql/06`; run as ACCOUNTADMIN).
- `github_pat` must live in `SURAKSHA.CORE` (or qualify it in `sql/06_git_repo.sql`).

## 1. PLAN (CoCo)
In Snowsight open Cortex Code (CoCo) and paste, one at a time:
1. "Read `@SURAKSHA.CORE.SURAKSHA_REPO/branches/main/docs/PRD.md` (after step 2) and summarise entities, detection rules and audit requirements." (Run after the repo exists; before that, paste the PRD text.)
2. "Explain which fields of sql/02_consortium_share.sql are shared vs private and why only hashes are safe."
3. "Check `RULE_WEIGHTS` in sql/04_rules.sql against `src/suraksha/config.py` RULE_WEIGHTS."
Evidence: screenshot each CoCo answer.

## 2. BUILD
1. Open a new SQL worksheet, paste the whole of `sql/06_git_repo.sql` (the bootstrap statements cannot come from Git because the repo does not exist yet; everything after them pulls from Git). Run all, as ACCOUNTADMIN. It: creates `SURAKSHA` + `SURAKSHA.CORE`, creates the Git repository, `FETCH`es, lists files (`LS`), then runs 00, 01, 02, 03, 04, 05, 07, 08, 09, 10 in order straight from Git.
   - Ordering note: the DB must exist before the repo object, hence the bootstrap at the top; `00_setup.sql` is idempotent and then takes ownership of DB/schemas for `SURAKSHA_ADMIN`.
   - Run it in pieces if you prefer (section A+B, check `LS`, then section C); if one `EXECUTE IMMEDIATE FROM` fails, fix the file, push, `ALTER GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO FETCH;`, re-run that file only.
2. Alternative with CoCo: "Run `sql/06_git_repo.sql` from my repo and report each error; do not change roles other than as the script does."
3. Grant yourself the roles:
   ```sql
   GRANT ROLE SURAKSHA_ADMIN TO USER <your_user>;
   GRANT ROLE SURAKSHA_APP   TO USER <your_user>;
   ```
Evidence: LS output; `SHOW SCHEMAS IN DATABASE SURAKSHA;` (6 schemas); `SHOW PROCEDURES LIKE '%' IN SCHEMA SURAKSHA.CORE;` shows `LOAD_SYNTH`, `RUN_PIPELINE`; `SHOW STREAMLITS;`.

## 3. RUN
```sql
USE ROLE SURAKSHA_ADMIN;  USE WAREHOUSE SURAKSHA_WH;
CALL SURAKSHA.CORE.LOAD_SYNTH(42);        -- JSON row counts; "package_from" says how the package was found

USE ROLE SURAKSHA_APP;
CALL SURAKSHA.CORE.RUN_PIPELINE(42);      -- JSON: detection_rate, false_positive_rate, counts_by_status, audit_chain_intact
```
- Run `RUN_PIPELINE` once per fresh database: the consortium ledger and audit log are append-only, a second run would see the first run's pledges. Reset = `DROP DATABASE SURAKSHA;` then re-run section 2 step 1.
- Expect minutes (several round trips per request). Suspend afterwards: `ALTER WAREHOUSE SURAKSHA_WH SUSPEND;`
- Open the app: Snowsight, Projects, Streamlit, `SURAKSHA_APP` (needs role SURAKSHA_APP or SURAKSHA_ADMIN). Inside Snowflake the app detects the Streamlit-in-Snowflake session and reads the live tables (PIPELINE_RESULTS, CASES, REPORTS, AUDIT_LOG, REGISTRY); approvals go through `DECIDE_CASE`.
- Faster alternative for demos: `CALL SURAKSHA.CORE.RUN_PIPELINE_BATCH(42, TRUE);` as SURAKSHA_ADMIN (section 6) — same results in ~37 statements / ~15 s.

## 4. TEST (as SURAKSHA_APP unless noted)

> **Secondary roles — read first.** Newer Snowflake accounts default users to `USE SECONDARY ROLES ALL`, so after
> `USE ROLE SURAKSHA_APP` a human user still carries the privileges of every other role it holds (e.g. ACCOUNTADMIN).
> Any "must fail with insufficient privileges" check is only meaningful with secondary roles off:
> ```sql
> USE ROLE SURAKSHA_APP;
> USE SECONDARY ROLES NONE;
> SELECT CURRENT_ROLE(), CURRENT_SECONDARY_ROLES();   -- SURAKSHA_APP, {"roles":""}
> SHOW GRANTS ON TABLE SURAKSHA.CORE.CASES;            -- SURAKSHA_APP: SELECT, INSERT only
> ```
> Live ITER-05: an `UPDATE CASES` "succeeded" as SURAKSHA_APP because ACCOUNTADMIN was active as a secondary role.

1. Rule parity, SQL vs Python (`RUN_PIPELINE` fills `INVESTIGATION_FACTS` and `PIPELINE_RESULTS`):
   ```sql
   SELECT p.request_id, p.py_score, c.score AS sql_score, p.py_band, c.band AS sql_band
   FROM SURAKSHA.CORE.PIPELINE_RESULTS p
   JOIN SURAKSHA.CORE.V_CONFIDENCE c USING (request_id)
   WHERE p.py_score <> c.score OR p.py_band <> c.band;          -- expect 0 rows
   SELECT COUNT(*) AS compared FROM SURAKSHA.CORE.V_CONFIDENCE;  -- > 0
   ```
2. Counts / outcome mix:
   ```sql
   SELECT scenario, status, COUNT(*) FROM SURAKSHA.CORE.PIPELINE_RESULTS GROUP BY 1,2 ORDER BY 1,2;
   ```
3. Audit chain: `SELECT * FROM SURAKSHA.CORE.V_AUDIT_VERIFY_SUMMARY;` (chain_intact = TRUE) and `DELETE FROM SURAKSHA.CORE.AUDIT_LOG;` must fail with insufficient privileges.
4. Consortium isolation: `SELECT * FROM SURAKSHA.CONSORTIUM.V_SHARED_LEDGER LIMIT 5;` shows hashes only, no names.
5. Cortex (optional): `SELECT AI_COMPLETE('claude-sonnet-4-5', 'Say OK');`
6. Gate G4 (human approval) enforced in SQL. Pick a pending case: `SELECT case_id FROM SURAKSHA.CORE.CASES WHERE status = 'PENDING_APPROVAL' LIMIT 2;`
   ```sql
   USE ROLE SURAKSHA_APP;
   USE SECONDARY ROLES NONE;   -- otherwise ACCOUNTADMIN privileges leak in and the UPDATE check is meaningless
   CALL SURAKSHA.CORE.DECIDE_CASE('<case_id>', 'APPROVE', 'system:bot', '');   -- must ERROR (system actor)
   CALL SURAKSHA.CORE.DECIDE_CASE('<case_id>', 'REJECT',  'Priya Nair', '');   -- must ERROR (reason required)
   CALL SURAKSHA.CORE.DECIDE_CASE('<case_id>', 'APPROVE', 'Priya Nair', '');   -- succeeds: FILED, hold_recommended TRUE
   CALL SURAKSHA.CORE.DECIDE_CASE('<case_id>', 'APPROVE', 'Priya Nair', '');   -- must ERROR (already decided)
   UPDATE SURAKSHA.CORE.CASES SET status = 'FILED' WHERE case_id = '<other_case_id>';  -- must fail: insufficient privileges
   SELECT * FROM SURAKSHA.CORE.V_AUDIT_VERIFY_SUMMARY;   -- chain_intact = TRUE; 3 new rows (CASE_APPROVED, CASE_FILED, HOLD_RECOMMENDED) by officer:Priya Nair
   ```
   Also `DELETE FROM SURAKSHA.CORE.CASES ...` fails, and `CALL SURAKSHA.CORE.LOAD_SYNTH(42)` as SURAKSHA_APP fails (admin only).

## 5. Evidence checklist (screenshots)
- [ ] CoCo PLAN answers (3)
- [ ] `LS @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/` after FETCH
- [ ] Successful run of 06 (statement list, no red)
- [ ] `SHOW PROCEDURES` / `SHOW STREAMLITS`
- [ ] `CALL LOAD_SYNTH(42)` JSON
- [ ] `CALL RUN_PIPELINE(42)` JSON (detection rate, FPR, status counts, audit_chain_intact)
- [ ] Parity query returning 0 rows
- [ ] `V_AUDIT_VERIFY_SUMMARY` and the failed `DELETE`
- [ ] `V_SHARED_LEDGER` (hash-only)
- [ ] G4 in SQL: `DECIDE_CASE` with `system:bot` errors, named officer succeeds, `UPDATE CASES` as SURAKSHA_APP fails
- [ ] The Streamlit app, each persona page
- [ ] Snowsight query history showing the calls (proves it ran in Snowflake)

## Known unknowns (verify live)
- `COPY FILES INTO @CORE.CODE FROM <git repo>` and `session.file.get` in a procedure (how the `suraksha` package is delivered; docs only show single-file `IMPORTS` from a git repo).
- `session.connection` (or private `session._conn._conn`) as a connector connection with `%s` binds.
- `GRANT READ ON GIT REPOSITORY`; `CREATE STREAMLIT ... FROM <git path>` copying the whole tree (warehouse runtime requires a bare `MAIN_FILE`, hence the root `streamlit_app.py` shim + `environment.yml`).
- `sql/09` targets the container runtime (`RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11'`, `COMPUTE_POOL = SYSTEM_COMPUTE_POOL_CPU`). Container runtime reads dependencies from `pyproject.toml` or `requirements.txt` (not `environment.yml`) and, per the docs, needs an external access integration for PyPI (`SURAKSHA_PYPI_EAI`, created in section A of the file). Warehouse-runtime variant is commented in the file as fallback.
- `DECIDE_CASE` (`sql/10`): Snowpark `session.sql(..., params=[...])` binds, `BEGIN/COMMIT` inside an owner's-rights proc, and the `number of rows updated` result column of `UPDATE`. App-side code that MERGE-upserts CASES (`save_case`) needs UPDATE and will now fail for SURAKSHA_APP: decisions must go through `DECIDE_CASE`, and new cases must be plain INSERTs.

## 6. Batch mode: `RUN_PIPELINE_BATCH` (ITER-05, sql/11)
`RUN_PIPELINE` (sql/08) issues ~1,900 statements; at ~0.5 s per round-trip inside a procedure it exceeds 20 minutes and a bigger warehouse does not help (latency-bound). The batch proc issues ~27 statements for seed 42 (11 reads + 16 bulk INSERTs; +10 DELETEs when RESET): it bulk-reads the registry, ledger, policy clauses, transactions and audit tail, screens every request in memory with the same `Suraksha` pipeline code (audit chain continues the stored chain), then bulk-INSERTs requests, fields, ledger entries, reports, cases, audit records, `PIPELINE_RESULTS` and `INVESTIGATION_FACTS`.
```sql
USE ROLE SURAKSHA_ADMIN;  USE WAREHOUSE SURAKSHA_WH;
-- after LOAD_SYNTH(42):
CALL SURAKSHA.CORE.RUN_PIPELINE_BATCH(42, TRUE);    -- repeatable: wipes the DEMO workflow tables first
CALL SURAKSHA.CORE.RUN_PIPELINE_BATCH(42, FALSE);   -- append only; errors "already have ledger pledges" if re-run
```
- Returns the `RUN_PIPELINE` JSON plus `statements_issued`, `proc_version` (`iter05-batch`) and `elapsed_s` = `{read, screen, write}`.
- **Trust model.** `EXECUTE AS OWNER` (SURAKSHA_ADMIN owns the ledger). It inserts ledger rows directly instead of 173 `SP_PLEDGE_BANK_*` calls: it is the trusted batch harness simulating all three banks. Live single-request intake still goes through the per-bank procedures. Granted to SURAKSHA_ADMIN only.
- **RESET = TRUE is destructive and demo-only**: deletes all rows of bank REQUESTS, REQUEST_FIELDS, REPORTS, CASES, PIPELINE_RESULTS, INVESTIGATION_FACTS, CONSORTIUM.LEDGER and AUDIT_LOG (synthetic data; registry and transactions untouched). The append-only rules bind the app role, not the owner; the reset exists so the demo is repeatable. Never use on real data.
- After a run the section 4 TEST queries apply unchanged (parity query, audit verify: `V_AUDIT_VERIFY_SUMMARY` checks the whole chain).
- Verify live (owner's-rights docs list no `USE` and "no LIST in JavaScript/Scripting handlers"; Python is not listed): `LIST` + `session.file.get` for package loading, `session.connection` cursor, ~1,500-bind multi-row `INSERT ... SELECT ... UNION ALL` statements, statement-size limits on the 50-row requests chunks.
