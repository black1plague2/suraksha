# CoCo evidence — BUILD phase, part 1 (2026-10-01)

**Prompt to Cortex Code:** execute `sql/00_setup.sql` … `sql/05_cortex.sql` from git repository
`SURAKSHA.CORE.SURAKSHA_REPO` (branch main) via `EXECUTE IMMEDIATE FROM`, in order, stop at first error, then list objects.

**Result: all six succeeded on the first live run, no errors.**

| Object type | Created |
|---|---|
| Schemas (6) | CORE, REGISTRY, CONSORTIUM, BANK_A, BANK_B, BANK_C — owner SURAKSHA_ADMIN |
| Tables (20) | CORE: AUDIT_LOG, CASES, INVESTIGATION_FACTS, POLICY_CLAUSES, REPORTS, REQUEST_FIELDS, RULE_PARAMS, RULE_WEIGHTS · REGISTRY: ADDRESSES, COMPANIES, CORP_OWNERS, PERSONS, ROLES · CONSORTIUM: LEDGER · BANK_A/B/C: REQUESTS, TRANSACTIONS |
| Views (7) | CONSORTIUM.V_SHARED_LEDGER (secure, hashes only) · CORE.V_AUDIT_VERIFY, V_AUDIT_VERIFY_SUMMARY, V_CONFIDENCE, V_DOC_EXTRACTION (AI_EXTRACT), V_RULE_EVIDENCE, V_STR_NARRATIVE_PROMPT (AI_COMPLETE) |
| Procedures (3) | CONSORTIUM.SP_PLEDGE_BANK_A/B/C (owner's-rights, bank id hard-coded) |

Prior step (worksheet): API integration `github_api` + `CREATE GIT REPOSITORY`; `LS …/branches/main/sql/` listed 00–09.

Screenshots: _add `docs/evidence/build_git_ls.png`, `docs/evidence/build_00-05.png`_

Next: BUILD part 2 — 07 LOAD_SYNTH, 08 RUN_PIPELINE, 09 Streamlit (container runtime), 10 DECIDE_CASE.
