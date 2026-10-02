# CoCo evidence — RUN phase (2026-10-02)

**Prompt to Cortex Code:** FETCH the git repo, deploy `sql/11_run_pipeline_batch.sql`, then as SURAKSHA_ADMIN
`CALL SURAKSHA.CORE.RUN_PIPELINE_BATCH(42, TRUE);`

**Result — live in Snowflake, identical to the local eval (`scripts/eval.py`, seed 42):**

| Metric | Live Snowflake | Local | PRD goal |
|---|---|---|---|
| Requests screened | 173 | 173 | — |
| Detection rate | **100%** (43/43) | 100% | ≥ 90% ✅ |
| False-positive rate | **1.54%** (2/130) | 1.5% | < 10% ✅ |
| CLEAR / PENDING_APPROVAL / NEED_MORE_EVIDENCE | 128 / 36 / 9 | 128 / 36 / 9 | — |
| STRs drafted (all fully cited) | 36 | 36 | G3 ✅ |
| Wall time (read / screen / write) | 3.2 s / 1.1 s / 10.0 s ≈ **14.3 s** | — | G2 < 5 min ✅ |
| Statements issued | **37** (incl. demo reset) | — | — |
| Audit hash chain | intact | intact | G4 ✅ |

- Both false positives are `decoy_hard_same_voyage_same_qty` (deliberately ambiguous: unrelated shippers, same vessel/voyage/
  commodity/quantity band) and land in NEED_MORE_EVIDENCE — nothing is filed without a named officer.
- Every `dup_weak_unlinked` → NEED_MORE_EVIDENCE (evidence too weak to accuse); every `dup_original_*` → CLEAR.
- `package_from: @SURAKSHA.CORE.CODE/src/ (25 files)` — confirms owner's-rights procs cannot LIST the git stage directly; the
  refreshed fallback copy (deployed by sql/11) is what made this work.

## Path to this result (live debugging loop, all diagnosed by CoCo from real errors)
qmark binds → None-as-'None' → per-row INSERT crawl → stale proc body → 76,703 registry calls → 20-min CoCo limit → stale package
fallback → **success**. 37 statements per full run, down from ~1,900 (per-request path) and 76,703 store calls (naïve).

Screenshot: _add `docs/evidence/run_batch_json.png`_
