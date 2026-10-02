# Suraksha

**Catches trade-finance duplicate financing before the money leaves the bank.** Suraksha flags when the same cargo is
pledged to more than one lender, proves the link between "unrelated" borrowers, and hands a compliance officer an
audit-ready, fully cited suspicious-transaction report to approve. Nothing is filed and no hold is recommended without a
named human officer.

Snowflake CoCo CLI Hackathon (GCC Edition) · Track: Risk, Fraud and Regulatory Intelligence Copilot · All data is synthetic.

## Results (live on Snowflake, identical to local)
| PRD goal | Target | Result |
|---|---|---|
| G1 detect seeded duplicates | ≥ 90% | **100%** (56/56, incl. phantom cargo + document mismatches) |
| G1 false-positive rate | < 10% | **1.5%** (2/136 — both deliberately ambiguous decoys, held for more evidence, not filed) |
| G2 alert → cited finding | < 5 min | **~74 s** for a single new request via the live inbox stream; ~14 s for a full batch (173–192 requests) |
| G3 every claim cited | no black box | 36 STRs, 0 uncited sentences; SQL rules reproduce Python scores exactly (0/58 mismatches) |
| G4 humans in control | named officer only | enforced in SQL: `DECIDE_CASE` refuses system actors; app role cannot UPDATE cases or DELETE audit |
| G5 CoCo in every phase | plan · build · run · test | [evidence](docs/evidence/) |

Robustness: two independent blind red-team sets (85 adversarial scenarios — prompt injection in documents, vessel typos,
transshipment, unicode look-alikes, split cargo, missing documents) → 100% recall, 0% false positives after fixes.

## How it works
```
request + docs → Intake (extract, quote untrusted text) → Fingerprint (salted hashes only)
  → Consortium match (secure share: no names, no amounts) → Investigator (ownership graph, txns, policy)
  → Confidence (deterministic weighted rules) → LOW: ask for evidence | HIGH: STR draft (FIU-IND, every sentence cited)
  → Approve/Reject by named officer (Streamlit / Slack / DECIDE_CASE) → hash-chained audit log
```
Details: [PRD](docs/PRD.md) · [Architecture](docs/ARCHITECTURE.md) · [Contracts](docs/CONTRACTS.md) · [Glossary](docs/GLOSSARY.md)

## CoCo in every phase
| Phase | What Cortex Code did | Evidence |
|---|---|---|
| PLAN | Reviewed PRD + SQL against the live account; found the container-runtime, grant and G4 gaps | [PLAN_coco_review.md](docs/evidence/PLAN_coco_review.md) |
| BUILD | Deployed `sql/00–11` from the Git repo; diagnosed every live error (qmark binds, NULL binds, stale code, trial-account egress) | [BUILD 00–05](docs/evidence/BUILD_coco_00-05.md) · [BUILD 07–11](docs/evidence/BUILD_coco_07-10.md) |
| RUN | Screened all 173 requests in Snowflake: 100% / 1.5% / 14 s | [RUN_coco_batch.md](docs/evidence/RUN_coco_batch.md) |
| TEST | Rule parity, G4 refusals, privilege denials, audit chain | [TEST_coco.md](docs/evidence/TEST_coco.md) |
| BUILD (authored) | CoCo wrote, deployed, tested and refined the monitoring layer itself (`sql/13`) | [MONITORING_coco.md](docs/evidence/MONITORING_coco.md) |
| RUN + TEST (ITER-06) | Phantom-cargo + document-mismatch rules, near-real-time inbox screening (74 s) — 9/9 PASS | [ITER06_live_coco.md](docs/evidence/ITER06_live_coco.md) |

## Reproduce
- **On Snowflake (browser only):** [docs/SNOWSIGHT_RUNBOOK.md](docs/SNOWSIGHT_RUNBOOK.md) — connect the Git repo, run `sql/06`,
  then `CALL SURAKSHA.CORE.LOAD_SYNTH(42); CALL SURAKSHA.CORE.RUN_PIPELINE_BATCH(42, TRUE);` and open the `SURAKSHA_APP` Streamlit app.
- **Locally (no credentials):**
  ```bash
  pip install -e ".[dev]"
  python -m pytest -q            # 404 tests
  python scripts/eval.py         # PRD goals
  python scripts/demo.py --approve "Priya Nair"   # narrated end-to-end
  ```

## How it was built
Opus 5.5 as master/integrator with parallel Sonnet and Haiku agents on disjoint modules, every iteration ending in docs,
logs and end-to-end tests: [iteration logs](logs/iterations/) · [agent logs](docs/agent-logs/) · [handoff log](docs/HANDOFF_LOG.md).
