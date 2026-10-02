# ITER-06 — Improvement wave 1 (2026-10-02, deadline ~1 day)

## Triage of external suggestions
Kept (Snowflake-native, explainable, works on a trial account): near-real-time screening, what-if simulator, physical-cargo
check, cross-document rules, UAE goAML template, monitoring (built by CoCo), threat model, glass-box positioning.
Rejected with reasons: ML/GNN scoring (meaningless on synthetic data, breaks glass box), live AIS/customs APIs (trial blocks
egress — simulated as a feed table), auto-tuned weights (ungoverned), TTL-caching "no match" (would miss the fraud), cross-bank
document embeddings (leaks content), HE/MPC (scope), Docker/CLI (off-platform).

## Team (parallel)
| Agent | Model | Delivered |
|---|---|---|
| new-rules | Sonnet | `R_NO_VESSEL_CALL` (phantom cargo vs `REGISTRY.VESSEL_CALLS`), `R_DOC_MISMATCH` (B/L vs invoice vs LC vs WR); stand-alone findings (no consortium match) → NEED_MORE_EVIDENCE, never auto-STR; SQL mirror (01, 04, 07, 08, 11); 5 new scenarios |
| streaming | Sonnet | `sql/12_streaming.sql`: REQUEST_INBOX + `SUBMIT_REQUEST` + APPEND_ONLY stream + 1-min task + `SCREEN_INBOX`; `SUBMIT_DEMO_DUPLICATE/CLEAN`; `store/incremental.py` (~25 statements per pass) |
| whatif-ui | Sonnet | "Policy what-if" page (threshold + per-rule weights, deltas, flips, marginal value per rule); 6-step evidence-chain view; risk metrics (exposure held, time to finding, analyst queue) |
| report-templates | Sonnet | `report_templates.py`: FIU-IND + UAE-goAML (all sentences cited; sections marked "verify with UAE FIU guidance" — official pages unreachable) |
| docs-positioning | Haiku | `THREAT_MODEL.md`, `PITCH.md` |
| **CoCo** | Snowflake Cortex Code | **`sql/13_monitoring.sql`** — authored, deployed, tested and refined by CoCo |

## Master review fixes
- Applied new-rules' `pipeline.py` hook (agents may not edit pipeline.py).
- **Phantom-cargo false-positive risk:** rule fired when the feed had no record of a vessel at all — incomplete AIS coverage is
  not proof of fake cargo. Now: vessel unknown to the feed → analyst ask (data gap); vessel known but no call for this
  voyage/port/date → evidence. New `vessel_in_feed` on all stores; +1 test.
- PITCH.md: removed unmeasured claims (cases/day, review minutes, "<10 min to hold", invented exposure figure).
- CoCo's alert refinement reviewed: DECIDE_CASE refusals excluded only for DECIDE_CASE (no masking elsewhere).

## Results
- `python -m pytest tests -q` → **404 passed**
- eval (seed 42, 192 requests): detection **100%** (56/56), FPR **1.5%** (2/136), 36 STRs, 0 citation errors, 5/5 goals PASS
- new scenarios: phantom ×5, doc_mismatch_qty ×4, doc_mismatch_value ×4 → NEED_MORE_EVIDENCE; decoys ×6 → CLEAR
- red-team v1 27/27 FPR 0/14 · v2 24/24 FPR 0/8, 0 crashes

## Needs live verification (next CoCo pass)
sql/01 VESSEL_CALLS + 04 ALTERs (ADD COLUMN IF NOT EXISTS, DROP NOT NULL); Stream/Task syntax and EXECUTE TASK grant (written
from memory); stream consume inside an owner's-rights proc; OBJECT_INSERT in SUBMIT_REQUEST; nested CALL from SUBMIT_DEMO;
Streamlit app re-created (09) to pick up new pages; Python↔SQL parity incl. stand-alone rows.

## Live (CoCo) — 9/9 PASS
Deploy of sql/01, 04, 07, 08, 11, 12, 09 clean on the first attempt (incl. the Stream/Task written without doc checks).
Batch: 192 requests, detection 1.0, FPR 0.0147, 134/36/22, audit intact; parity 0/58; near-real-time duplicate screened
~74 s after submission. Evidence: `docs/evidence/ITER06_live_coco.md`.
