# CoCo evidence — PLAN phase (2026-10-01)

Cortex Code (Snowsight, online) reviewed the repo's PRD, runbook and `sql/00–09` against the live account
(Enterprise, AWS us-east-2, ACCOUNTADMIN) before anything was executed. Prompt and findings below.

**Prompt:** "Read docs/SNOWSIGHT_RUNBOOK.md, docs/PRD.md and every file in sql/ from the git repository
SURAKSHA.CORE.SURAKSHA_REPO (branch main). Review the design against the PRD and list anything that won't work on this
Enterprise account in AWS us-east-2 (Cortex model availability, privileges, Streamlit runtime). Don't execute anything yet."

## CoCo verified live
- `claude-sonnet-4-5` available; AI_EXTRACT and AI_COMPLETE work; cross-region Cortex already `ANY_REGION`.
- Role hierarchy, `SNOWFLAKE.CORTEX_USER` grant, `CREATE SHARE`, `CREATE GIT REPOSITORY`, `EXECUTE IMMEDIATE FROM`, `COPY FILES` from a git stage: all fine on this edition.
- Audit hash-chain table design and SQL rule views (`V_RULE_EVIDENCE`, `V_CONFIDENCE`) need no special features.

## CoCo findings → actions (ITER-04)
| # | Finding | Severity | Action |
|---|---|---|---|
| 1 | Account defaults to **container** Streamlit runtime (`SYSTEM_COMPUTE_POOL_CPU`); `09_streamlit.sql` used warehouse-runtime syntax | HIGH | 09 rewritten for container runtime |
| 2 | Python procs load the package via `COPY FILES` + `session.file.get` + private `session._conn._conn` shim | MEDIUM | verify in BUILD; fix from live errors |
| 3 | Streamlit app uses in-memory data, not the Snowflake tables | KNOWN GAP | app auto-detects SiS session and reads live tables |
| 4 | `GRANT READ ON GIT REPOSITORY` syntax | LOW | verify in BUILD |
| 5 | `LOAD_SYNTH` (EXECUTE AS CALLER + TRUNCATE) granted to SURAKSHA_APP, which can't truncate | LOW | admin-only grant |
| PRD | G4 (human approval) enforced only in Python, not in SQL | — | new `DECIDE_CASE` proc; app role loses UPDATE on CASES |

Screenshot: _add `docs/evidence/plan_coco.png`_
