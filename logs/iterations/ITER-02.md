# ITER-02 — Red-team, STR quality, Streamlit app, demo + Slack (2026-10-01)

## Team (parallel)
| Agent | Model | Delivered |
|---|---|---|
| red-team | Sonnet | `tests/adversarial/` (44 scenarios, written blind to detector code), `scripts/redteam_eval.py` |
| report-approval r2 | Sonnet | policy clauses filtered by fired rules; reporting entity from `bank_id`; cited PART 5 summary; +7 tests |
| match-engine r2 | Sonnet | new shared key `blv` (vessel-independent); voyage normalisation fix ("066S" = "V.066S" = "66S") |
| streamlit-app | Sonnet | `app/streamlit_app.py` — 4 persona pages (analyst queue, investigator Q&A + graph, MLRO approve/reject + audit verify, risk head KPIs); AppTest smoke tests |
| demo-slack | Haiku | `scripts/demo.py` (narrated judge demo), `scripts/slack_server.py` (signed Slack interactivity endpoint) |

## Results
| Check | Result |
|---|---|
| `python -m pytest tests -q` | **184 passed** |
| `scripts/eval.py` (seed 42) | detection **100%**, FPR **1.5%**, 36 STRs, 0 citation errors, all 5 goals PASS |
| `scripts/redteam_eval.py` before fix | recall 96.3% (26/27), FPR 0% — miss: `dup_vessel_typo` |
| `scripts/redteam_eval.py` after fix | recall **100%** (27/27), FPR **0%** (0/14) |
| `scripts/demo.py --approve "Priya Nair"` | full 7-step narrative, case FILED, "Audit chain: VALID", 88 ms |
| Streamlit (browser, localhost:8599) | analyst queue renders; MLRO page renders STR; approval via UI moved 36→35 pending / 1 decided |

## Findings & fixes
- **Red-team miss (real):** one-letter vessel typo changed every hash. Fixed with `blv` key; also found and fixed voyage-format bug.
- **Honesty note:** the red-team set has now been used to tune the matcher, so it is no longer blind. ITER-03 needs a fresh blind set.
- **UI bug:** adjacent footnotes rendered as `[16]****[9]` → fixed (trailing space after bold marker); app smoke 6 passed.
- Red-team "either" cases: split-halves cargo → PENDING_APPROVAL; unrelated same-voyage same-qty → NEED_MORE_EVIDENCE; all-ids-missing → PENDING_APPROVAL.
- Shell 4+ hops from the UBO lands in NEED_MORE_EVIDENCE (graph hop limit 4) — acceptable, documented.

## Backlog → ITER-03
1. Live Snowflake via CoCo (PLAN/BUILD/RUN/TEST with evidence) — verify the 6 unverified SQL items from ITER-01b.
2. Fresh blind red-team set incl. prompt-injection text in documents (matters once AI_EXTRACT reads docs), all-docs-missing, invoice/LC currency mismatch.
3. Demo polish: names instead of ids; cleaner path rendering.
4. Streamlit-in-Snowflake variant of the app (SnowflakeStore backend).
