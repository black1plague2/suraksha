# Testing

Every iteration ends with all three levels green, results pasted into `logs/iterations/ITER-NN.md`.

| Level | Command | What it proves |
|---|---|---|
| Unit | `python -m pytest tests/unit -q` | each agent module against its contract |
| End-to-end | `python -m pytest tests/e2e -q` | full pipeline on the synthetic consortium: G1–G4, privacy of consortium rows |
| Eval | `python scripts/eval.py` | PRD goal numbers + per-scenario breakdown; writes `logs/eval/latest.json`; exit 1 on any FAIL |
| Snowflake (live) | `python -m pytest -m snowflake` | backend parity on a real account (skipped without `SNOWFLAKE_*` env) |

## Goal → test mapping
| Goal | Test |
|---|---|
| G1 ≥90% detection, <10% FPR | `test_g1_detection_rate`, `test_g1_false_positive_rate`, eval |
| G2 <5 min alert→finding | `test_g2_latency_under_five_minutes`, eval latency |
| G3 every claim cited | `test_g3_every_str_fully_cited`; pipeline raises on uncited sentence |
| G4 human in control | `test_g4_human_approval_and_audit`, `test_reject_path_closes_case` |
| Privacy | `test_consortium_holds_no_names_or_amounts` |
| Weak evidence | `test_weak_unlinked_asks_for_evidence` |

## Synthetic scenarios
See `docs/CONTRACTS.md` §A. Hard negatives (`decoy_hard_same_voyage_same_qty`) are kept deliberately; if they
turn into false positives that is reported honestly in the eval, not tuned away.
