# CoCo evidence — monitoring layer authored by Cortex Code (2026-10-02)

Unlike the rest of the repo (written by the Claude agent team and deployed/tested by CoCo), this feature was **designed,
written, deployed and tested by Snowflake Cortex Code itself** from a one-paragraph brief. Its SQL is committed verbatim as
`sql/13_monitoring.sql` (header: "authored by Snowflake Cortex Code (CoCo)").

## What CoCo built
| Object | Type | Purpose |
|---|---|---|
| `CORE.MON_EVENTS` | table | alert event sink (time, severity, source, check_name, detail) |
| `CORE.MON_PIPELINE_HEALTH` | view | recent CALLs of Suraksha procedures from `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, p50/p95 durations |
| `CORE.MON_DATA_QUALITY` | view | requests with missing key fields, ledger rows with missing keys, stale pending cases |
| `CORE.ALERT_AUDIT_CHAIN_BROKEN` | alert, 5 min | CRITICAL when `V_AUDIT_VERIFY_SUMMARY` reports a broken chain |
| `CORE.ALERT_PROC_FAILURE` | alert, 5 min | HIGH when a procedure failed in the last hour |
| `CORE.ALERT_STALE_PENDING_CASES` | alert, 5 min | MEDIUM when a case has been pending > 24 h |

CoCo's own design decisions: `INFORMATION_SCHEMA.QUERY_HISTORY()` is a table function and can't back a view, so it used
`ACCOUNT_USAGE.QUERY_HISTORY` (granted access); `SCREEN_INBOX` didn't exist yet, so it was included conditionally.

## CoCo's test results
| Check | Result |
|---|---|
| `MON_PIPELINE_HEALTH` | LOAD_SYNTH p50 ≈ 11 s · RUN_PIPELINE_BATCH p50 ≈ 31 s, p95 ≈ 39 s · DECIDE_CASE p50 ≈ 5.2 s |
| `MON_DATA_QUALITY` | 173 requests, 0 missing fields · 173 ledger rows, 0 missing keys · 0 stale cases |
| Audit-chain alert condition | 0 rows (chain intact → silent) ✅ |
| Stale-cases alert condition | 0 rows ✅ |
| Proc-failure alert condition | 5 rows — would fire |

## Review finding (master) → refinement
The 5 "failures" include the intentional `DECIDE_CASE` refusals from the TEST phase (system actor, reason-less reject,
double decision). Those are the G4 control working, so counting them would make the alert cry wolf. CoCo was asked to exclude
those refusal messages and keep genuine errors. **CoCo's refinement:** the exclusion is scoped to `DECIDE_CASE` calls only (a
global message filter could hide a real failure elsewhere that happened to contain the same words). Re-tested:
- last hour: 1 row left — the genuine `ModuleNotFoundError` from RUN_PIPELINE_BATCH; all G4 refusals gone;
- last 24 h: 9 rows, all genuine (1 ModuleNotFoundError, 8 LOAD_SYNTH: the `%s` and `None` errors + 6 cancelled long runs).

Note: `ACCOUNT_USAGE` views lag by up to ~45 min, so `MON_PIPELINE_HEALTH` is for trend monitoring, not second-by-second.
Screenshots: _add `docs/evidence/monitoring_views.png`_
