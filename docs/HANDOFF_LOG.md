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

## 2026-10-02 · ITER-07 · New app look (calm, plain-language) — built + checked locally
- Design approved by user: https://claude.ai/artifact/8hKwGvahNNJLUEx9MSwfAC (Source Serif 4 + Manrope, "Midnight & apricot" on cool mist #EEF1F5, short headings, data only).
- App rebuilt to match (app/ui.py tokens/CSS, .streamlit/config.toml). Master fixes after browser check: headline the STRONGEST ownership link (owner > ownership > director > shareholder > address > phone), "capped from" score total, calm casing of document text, exposure KPI per currency, nav/brand/chain layout.
- All pages load locally with no errors. Commit history rewritten to plain language (backup bundle kept in the session scratchpad).
- NEXT (user): in Snowsight run `ALTER GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO FETCH;` then `EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/09_streamlit.sql;` and check the app in Snowflake (SiS Streamlit version may differ — check nav pills/sticky card). Then screenshots into docs/evidence, rehearse docs/PITCH.md, suspend SCREEN_INBOX_TASK after judging.

## 2026-10-02 · ITER-06 · Wave 1 built locally ✅ (404 tests, 100%/1.5%) — live deploy next — see logs/iterations/ITER-06.md
- Triage of external suggestions done (kept: Snowflake-native + explainable; rejected: ML/GNN scoring, external APIs (trial has no
  egress), auto-tuned weights, TTL caching of no-match, cross-bank embeddings, HE/MPC, Docker/CLI — reasons in chat/ITER-06 log).
- Agents running: new-rules (Sonnet: R_NO_VESSEL_CALL phantom cargo + R_DOC_MISMATCH, SQL mirror; master must apply its pipeline.py diff),
  streaming (Sonnet: REQUEST_INBOX + Stream + Task + SCREEN_INBOX, sql/12), whatif-ui (Sonnet: what-if simulator, evidence chain, risk metrics),
  report-templates (Sonnet: UAE goAML STR mapping), docs-positioning (Haiku: THREAT_MODEL.md, PITCH.md).
- CoCo itself builds monitoring (user pastes its SQL back → commit as sql/13, credited to CoCo).

## 2026-10-02 · ITER-05 · COMPLETE — PLAN/BUILD/RUN/TEST all done live with CoCo ✅
- TEST run 2: 9/9 PASS (parity 0/45, G4 refusals, UPDATE/DELETE denied with secondary roles NONE, audit 567 rows intact).
- README rewritten for judges; evidence in `docs/evidence/`; close-out in `logs/iterations/ITER-05.md` (open items listed there).
- Demo data state: last reset + run via RUN_PIPELINE_BATCH(42, TRUE); one case (CASE-REQ-00016) approved by "Priya Nair".
- To resume: add screenshots, walk through the SURAKSHA_APP Streamlit app in Snowsight, then pick from ITER-05 open items.

## 2026-10-02 · ITER-05 · LIVE RUN ✅ — Snowflake results identical to local
- `CALL RUN_PIPELINE_BATCH(42, TRUE)`: detection 100%, FPR 1.54%, 36 STRs, audit chain intact, 37 statements, ~14 s.
- Evidence so far: PLAN, BUILD (00–05, 07–11), RUN in `docs/evidence/`. 354 tests pass.
- NEXT: CoCo TEST prompts (rule parity SQL↔Python, DECIDE_CASE G4 refusals, UPDATE CASES denied, audit verify) → open the
  Streamlit app → final evidence pack + README for judges.

## 2026-10-02 · ITER-04 → ITER-05 · LOAD_SYNTH live ✅, RUN_PIPELINE too slow inside a proc
- Live: sql/00–10 deployed; LOAD_SYNTH(42) loads 234 companies / 3908 txns (matches local). Streamlit app created (warehouse runtime).
- RUN_PIPELINE(42) exceeds CoCo's 20-min call limit (~1,900 statements, latency-bound). User running it from a worksheet.
- In progress: batch-mode agent → `sql/11_run_pipeline_batch.sql` (target ~50 statements). Then RUN + TEST prompts (in chat history /
  SNOWSIGHT_RUNBOOK) for CoCo evidence, then final evidence pack.

## 2026-10-01 · ITER-04 · CoCo PLAN done, BUILD 00–05 live ✅, fixes pushed for 07–10
- Evidence: `docs/evidence/PLAN_coco_review.md`, `docs/evidence/BUILD_coco_00-05.md`. 336 tests pass.
- NEXT: user FETCHes, CoCo runs sql/07, 08, 09, 10 → then RUN (`CALL LOAD_SYNTH(42)`, `CALL RUN_PIPELINE(42)`, open app) → TEST.
- Details: `logs/iterations/ITER-04.md`.

## 2026-10-01 · ITER-03 · DONE locally — 322 tests, red-team v1+v2 100%/0% FPR; live Snowflake BUILD started
- Git repo connected in Snowsight (LS shows sql/00–09). Waiting on Cortex Code PLAN output from the user.
- Details + ITER-04 backlog: `logs/iterations/ITER-03.md`.

### ITER-03 working notes
- User works browser-only in Snowsight + Cortex Code online. Account details are kept out of the repo (see local notes); Enterprise edition, role ACCOUNTADMIN.
- Code → Snowflake via Git repository object. User chose a **public repo** for the hackathon; the user flips visibility
  themselves. sql/06 keeps `GIT_CREDENTIALS` as one removable line. Account identifiers are NOT stored in the repo.
- Done: snowflake-native (Sonnet) — sql/06 git repo + EXECUTE IMMEDIATE FROM deploy chain, sql/07 LOAD_SYNTH proc, sql/08
  RUN_PIPELINE proc (+ PIPELINE_RESULTS for SQL/Python rule parity), sql/09 Streamlit-in-Snowflake, root `streamlit_app.py`
  shim + `environment.yml`, `docs/SNOWSIGHT_RUNBOOK.md`; 29 static SQL tests. Several items "verify live" (see its agent log).
- Done: red-team-v2 (Sonnet, blind) — recall 95.8% (23/24), FPR 0%, no crashes. Real findings: (1) injected document text
  echoed unquoted in STR → injection-hardening agent running; (2) transshipment via invoice B/L Ref missed → match-engine r3
  running (`bln` shared key + `bln_ref` probe). Master added `ExtractedFields.bl_ref`; replaced RotatingFileHandler (Windows rollover error).
- Done: demo-polish (Haiku) — names, readable ownership chains, "why this matters".
- Note: Streamlit-in-Snowflake app still uses the in-memory backend (reads no Snowflake tables yet) → ITER-04.

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
