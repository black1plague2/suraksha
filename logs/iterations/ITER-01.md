# ITER-01 — Foundation + parallel module build (2026-10-01)

## Team
| Agent | Model | Delivered | Unit tests |
|---|---|---|---|
| master | Opus 5.5 | contracts (`models.py`, `store/base.py`, `store/memory.py`, `config.py`, `log.py`), `pipeline.py`, `scripts/eval.py`, `tests/e2e/`, PRD/ARCHITECTURE/TESTING/CONTRACTS/HANDOFF docs | — |
| synth-data | Sonnet | `synth/generator.py`, `synth/templates.py` | 10 passed |
| match-engine | Sonnet | `agents/intake.py`, `agents/fingerprint.py` | 30 passed |
| investigator | Sonnet | `agents/investigator.py`, `agents/linkage.py`, `agents/confidence.py` | 20 passed |
| report-approval | Sonnet | `agents/report.py`, `agents/approval.py`, `integrations/slack.py` | 28 passed |
| docs-coco | Haiku | `docs/COCO_USAGE.md`, `docs/RUNBOOK.md`, `docs/GLOSSARY.md` | n/a |
| snowflake-infra | Sonnet | `sql/00–05`, `store/snowflake.py`, `integrations/cortex.py` | still running at commit time → ITER-01b |

## Integration results
- `python -m pytest tests -q` → **97 passed in 1.38s** (unit 88 + e2e 9; snowflake-infra tests not yet included)
- `python scripts/eval.py` (seed 42, 173 requests):

| Metric | Result | Goal |
|---|---|---|
| Detection rate | **100.0%** (43/43) | ≥ 90% ✅ |
| False-positive rate | **1.5%** (2/130) | < 10% ✅ |
| Latency p50 / max | 0.49 / 29.9 ms | < 5 min ✅ |
| STRs drafted / citation errors | 36 / 0 | 0 errors ✅ |
| Human-in-control + audit chain | pass | ✅ |

- Seeds 1, 7, 13, 99, 2026: detection 100%, FPR 1.5% on every seed.
- The 2 FPs are both `decoy_hard_same_voyage_same_qty` (unrelated shippers, same vessel/voyage/commodity/band). They land in
  NEED_MORE_EVIDENCE, not an STR — the right outcome for a genuinely ambiguous case.
- Every `dup_weak_unlinked` → NEED_MORE_EVIDENCE with concrete asks (PRD "tell me when evidence is weak").

## Honest caveats
- Generator and detector were written from the same contract, so the 100% is an upper bound. ITER-02 adds an
  **independent adversarial test set** written by an agent that does not see the detector code.
- No live Snowflake run yet (no account / CoCo connected).

## Issues found in review (→ ITER-02)
1. STR cites TF-4.2 (re-issued B/L) even for EXACT matches — filter policy clauses by fired rules.
2. STR PART 1 reporting entity is a fixed config string; should follow `req.bank_id`.
3. Contract/deviation notes: synth adds a `decoy_anchor` scenario (base requests for decoys); fingerprint adds private
   `cargo_m1/cargo_p1` keys (stripped before sharing); `draft_str` takes `store=` kwarg (pipeline now passes it).
4. report-approval agent's write of its own log was refused by the tool — master saved it from the hand-back.

## Master changes during integration
- `pipeline.py` passes `store=` to `draft_str`; `config.py` adds `SURAKSHA_SLACK_SIGNING_SECRET` (RUNBOOK referenced it).
- CONTRACTS.md: citation conventions + private fingerprint keys documented.
