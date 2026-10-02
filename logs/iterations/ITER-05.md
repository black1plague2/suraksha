# ITER-05 — Batch mode, live RUN + TEST complete (2026-10-02)

(Detailed live debugging trail for ITER-04/05 is in `ITER-04.md`; this file is the close-out.)

## Delivered
- `RUN_PIPELINE_BATCH(SEED, RESET)` (sql/11, `store/batch.py`): bulk read → in-memory screening → bulk write; 37 statements, ~14 s.
- Package loader hardening (required-module check, fallback refresh, loud errors); registry snapshot cache; qmark + NULL binds.
- Runbook: secondary-roles rule for privilege tests; app now documented as reading live tables.
- Evidence pack complete: `docs/evidence/` PLAN · BUILD (00–05, 07–11) · RUN · TEST. README rewritten for judges.

## Live results (Snowflake, trial Enterprise account, AWS us-east-2)
| Check | Result |
|---|---|
| RUN_PIPELINE_BATCH(42) | detection 100% (43/43), FPR 1.54% (2/130), 36 STRs, audit intact, 37 stmts, 14.3 s — identical to local |
| Rule parity SQL↔Python | 0 mismatches / 45 |
| G4 in SQL (DECIDE_CASE) | system actor, empty-reason reject, double decision all refused; named officer files + hold |
| Privileges (secondary roles NONE) | app role: UPDATE CASES and DELETE AUDIT_LOG → insufficient privileges |
| Audit chain | 567 rows, 0 breaks |

## Local
`python -m pytest tests -q` → **354 passed** · eval 5/5 PASS · red-team v1 27/27, v2 24/24, FPR 0%.

## Open items (post-hackathon / nice-to-have)
1. Streamlit-in-Snowflake app on live data: created; needs a visual walkthrough + screenshots (user).
2. Screenshots referenced in `docs/evidence/*.md` to be added by the user.
3. Consolidate invoice "B/L Ref" parsing into intake (fingerprint keeps a temporary regex).
4. SQL-side full content re-hash of the audit chain (today: SQL checks linkage, Python re-hashes).
5. Per-request RUN_PIPELINE (~0.5 s/statement inside procs) is the real-time path; fine per request, slow for 173 in a row.
