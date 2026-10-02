# CoCo evidence — TEST phase (2026-10-02)

All checks run by Cortex Code in Snowsight against the live deployment.

## Run 1
| Check | Result |
|---|---|
| Rule parity, Python vs SQL (`PIPELINE_RESULTS` ⋈ `V_CONFIDENCE`) | **PASS** — 0 mismatches, 45 compared |
| `DECIDE_CASE` refuses `system:bot` | **PASS** — "decisions must be made by a named human officer, not a system actor" |
| `DECIDE_CASE` refuses REJECT without reason | **PASS** — "a reason is required to reject" |
| Named officer approves → FILED + hold | **PASS** — `decided_by = officer:Priya Nair`, `hold_recommended = TRUE` |
| Double decision refused | **PASS** — "case … already decided (FILED)" |
| `UPDATE CASES` as SURAKSHA_APP must fail | **FAIL** — 1 row updated |
| Audit chain | **PASS** — 567 rows, 0 breaks |

**Root cause of the FAIL (not a grant bug):** the repo grants SURAKSHA_APP only SELECT + INSERT on CASES (`sql/01`, re-asserted
in `sql/10`). The test ran as a human user whose session had `USE SECONDARY ROLES ALL` (Snowflake's default for newer
accounts), so ACCOUNTADMIN's privileges applied despite `USE ROLE SURAKSHA_APP`. The runbook now requires
`USE SECONDARY ROLES NONE` before any privilege check. The stray UPDATE left one case FILED without an officer or audit row,
so the demo data was restored with `RUN_PIPELINE_BATCH(42, TRUE)` before run 2.

## Run 2 (secondary roles NONE)
| # | Check | Result |
|---|---|---|
| 1 | Reset + re-screen | **PASS** — detection 1.0, audit chain intact |
| 2 | `SHOW GRANTS ON TABLE CASES` | SURAKSHA_APP: SELECT, INSERT only |
| 3 | G4: `system:bot` APPROVE refused | **PASS** |
| 4 | G4: REJECT with empty reason refused | **PASS** |
| 5 | G4: named officer APPROVE → FILED, hold TRUE | **PASS** |
| 6 | G4: double decision refused | **PASS** |
| 7 | `UPDATE CASES` → insufficient privileges | **PASS** |
| 8 | `DELETE FROM AUDIT_LOG` → insufficient privileges | **PASS** |
| 9 | Audit chain (`V_AUDIT_VERIFY_SUMMARY`) | **PASS** — 567 rows, 0 breaks |

Last audit rows: 565 CASE_APPROVED · 566 CASE_FILED · 567 HOLD_RECOMMENDED — all `officer:Priya Nair`, subject CASE-REQ-00016.

## What this proves
- **G4 (humans stay in control) is enforced in the database**, not just in Python: the only way to change a case is
  `DECIDE_CASE`, which refuses system actors, blank names, reason-less rejections and repeat decisions.
- **Audit log is tamper-resistant:** the app role cannot delete or update it; the hash chain verifies end to end.
- **Explainability holds in SQL:** the deterministic rules in `sql/04_rules.sql` reproduce the Python scores exactly.

Note: `V_AUDIT_VERIFY` checks seq continuity and prev-hash linkage in SQL; full content re-hashing is done by Python
`AuditLog.verify()` (the procs report `audit_chain_intact` from it).

Screenshots: _add `docs/evidence/test_run2.png`_
