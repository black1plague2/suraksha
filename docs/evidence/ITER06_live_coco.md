# CoCo evidence — ITER-06 live deploy + test (2026-10-02)

Cortex Code deployed `sql/01, 04, 07, 08, 11, 12, 09` from the Git repo and ran every check. **9/9 PASS, no errors.**

| # | Step | Result |
|---|---|---|
| 1 | `ALTER GIT REPOSITORY … FETCH` | PASS (fast-forward) |
| 2 | `EXECUTE IMMEDIATE FROM` sql/01, 04, 07, 08, 11, 12, 09 | PASS — all seven |
| 3 | `LOAD_SYNTH(42)` (`iter06-newrules`) | PASS — 253 companies, 4,331 transactions, **268 vessel calls**, 192 requests generated |
| 4 | `RUN_PIPELINE_BATCH(42, TRUE)` (`iter06-batch-newrules`) | PASS — detection **1.0** (56/56), FPR **0.0147** (2/136); 134 CLEAR / 36 PENDING_APPROVAL / 22 NEED_MORE_EVIDENCE; audit chain intact |
| 5 | Python↔SQL rule parity | PASS — **0 mismatches / 58 compared** (incl. stand-alone rows) |
| 6 | `SUBMIT_DEMO_DUPLICATE('BANK_B')` | PASS |
| 7 | `REQUEST_INBOX` row | PASS — SCREENED, result PENDING_APPROVAL |
| 8 | `PIPELINE_RESULTS` row | PASS — PENDING_APPROVAL, score 1.0, HIGH |
| 9 | `SCREEN_INBOX_TASK` history | PASS — run SUCCEEDED; previous run SKIPPED (stream empty, as designed) |

## Near-real-time screening, live
Request `LIVE-20261002165144` submitted 09:51:44 → task run 09:52:39–09:52:59 → **screened 09:52:58, ~74 s after submission**.
Rules fired: R_EXACT_HASH, R_SHARED_UBO, R_SHARED_DIRECTOR, R_SAME_ADDRESS, R_SAME_PHONE → cited STR + case pending a named officer.
(The 1-minute task schedule sets the latency floor; the screening run itself took ~20 s.)

## New rules, live
`phantom_no_vessel_call` ×5 and `doc_mismatch_qty/value` ×8 → NEED_MORE_EVIDENCE (never an auto-drafted STR without a consortium
match); decoys `decoy_minor_rounding` ×3 and `decoy_vessel_call_edge` ×3 → CLEAR. Same numbers as the local eval.

Notes: live demo submissions carry scenario `LIVE` and `label_duplicate = FALSE` (no ground truth), so they are excluded from
the confusion matrix. Streamlit app re-created from the new code (09) — UI check pending.
Cost note: suspend the task after the demo — `ALTER TASK SURAKSHA.CORE.SCREEN_INBOX_TASK SUSPEND;`
Screenshots: _add `docs/evidence/iter06_inbox.png`, `docs/evidence/iter06_task_history.png`_
