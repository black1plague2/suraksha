# Handoff Log

Read this first when resuming. Newest entry on top. Every iteration MUST add an entry before the session ends
(usage limit, context reset, or handoff). Per-iteration detail lives in `logs/iterations/ITER-NN.md`.

## Resume checklist
1. `git pull` · `python -m pytest -q` (all green?) · `python scripts/eval.py` (G1/G2 numbers)
2. Read the newest entry below + the newest `logs/iterations/ITER-NN.md`
3. Check "Open items" and "Contract change requests"

## Contract change requests
_(agents append here; master applies and records the resolution)_

---

## 2026-10-01 · ITER-02 · Red-team + app + demo — 184 tests, all goals PASS
- Blind red-team found 1 real miss (vessel typo) → fixed (`blv` key); red-team now 27/27, FPR 0% (no longer blind).
- Streamlit app (4 personas) verified in browser incl. a UI approval; `scripts/demo.py` narrated judge demo; Slack endpoint.
- User has CoCo access ($400 credits on a friend's Snowflake account) and is logging in. `cortex`/`snow` not yet on PATH in Claude's shell.
- NEXT: ITER-03 — CoCo live deploy (see `docs/COCO_USAGE.md`, `docs/SNOWFLAKE_DEPLOY.md`), fresh blind red-team, demo polish. Details: `logs/iterations/ITER-02.md`.

## 2026-10-01 · ITER-01 · Integrated — ALL PRD GOALS PASS LOCALLY
- 97 tests green; eval seed 42: detection 100%, FPR 1.5%, 0 citation errors, audit chain OK. Details: `logs/iterations/ITER-01.md`.
- snowflake-infra agent still running at this commit; its files land in ITER-01b.
- NEXT (ITER-02, parallel): (a) independent adversarial test set (agent blind to detector code); (b) STR fixes — policy
  clauses filtered by fired rules, reporting entity from bank_id; (c) Streamlit app (analyst/investigator/MLRO/risk-head
  views + "who is linked to X?"); (d) `scripts/demo.py`; (e) Slack interactivity endpoint; (f) live Snowflake via CoCo once user installs it.

## 2026-10-01 · ITER-01 · Foundation + parallel build (started)
- Repo created: https://github.com/black1plague2/suraksha (private). Local: `Documents\suraksha`.
- Master (Opus) wrote contracts: `models.py`, `store/base.py`, `store/memory.py`, `config.py`, `log.py`, `docs/CONTRACTS.md`, `docs/PRD.md`.
- Agents dispatched in parallel: synth-data (Sonnet), match-engine (Sonnet), investigator (Sonnet), report-approval (Sonnet), snowflake-infra (Sonnet), docs-coco (Haiku).
- Environment: no Snowflake CLI / connection yet; user is providing CoCo CLI. Everything is built to run locally on MemoryStore; Snowflake layer is written but untested live.
- Open items: pipeline.py + e2e tests + eval (master, after agents) · Streamlit app (ITER-02) · live Snowflake deploy via CoCo (needs account).
