# streamlit-app log
- Built app/streamlit_app.py (4 personas), app/README.md, tests/unit/test_app_smoke.py (AppTest per page).
- Full suite: 184 passed.
- Notes: st.markdown lacks footnotes, so [^n] markers are rendered as bold [n]. Status badge shows FILED/CLOSED once a case is decided.

## Round 2 (ITER-04)
- Problem: under Streamlit-in-Snowflake the app used the in-memory backend and ignored LOAD_SYNTH/RUN_PIPELINE data.
- `detect_backend()`: `snowflake.snowpark.context.get_active_session()` succeeds -> Snowflake mode; else `SURAKSHA_BACKEND` (`snowflake` = connector via env, default `memory`). Active backend shown in sidebar.
- Snowflake mode: `SnowflakeStore` over `_Conn(session)` (DB-API shim copied from sql/08). Read-only views from CORE.PIPELINE_RESULTS + bank REQUESTS + REQUEST_FIELDS + CASES/REPORTS/AUDIT_LOG/REGISTRY; empty PIPELINE_RESULTS -> "Run CALL SURAKSHA.CORE.RUN_PIPELINE(42) first". Pipeline is never re-run.
- Approve/Reject -> `CALL SURAKSHA.CORE.DECIDE_CASE(case_id, decision, officer, reason)` (sql/10); errors (or a returned `{"error": ...}`) shown verbatim. Memory mode still uses `Suraksha.decide`.
- Verify audit chain (Snowflake): `AuditLog.verify()` over `store.list_audit()` with naive TIMESTAMP_NTZ values re-tagged UTC (hash was computed over aware UTC), plus breaks count from CORE.V_AUDIT_VERIFY.
- environment.yml: added snowflake-snowpark-python. Tests: +fake-session smoke tests (detection, empty results, 4 pages, verify, decide routing, decide error). Suite: 336 passed.
- Needs live verification: DECIDE_CASE return shape (parsed as JSON; `error` key => refusal), `session.connection` vs `session._conn._conn` in SiS, TIMESTAMP_NTZ hash round-trip, SiS container vs warehouse runtime package availability.

## Round 3 (ITER-06)
- New page "Policy what-if" (`page_whatif`, pure `simulate()` / `whatif_frame()`): numpy/pandas re-score from fired rules, sliders default from `config.RULE_WEIGHTS` / `Settings.confidence_threshold`; metrics, confusion matrix, delta, band flips, marginal value per rule (weight -> 0). Memory labels via `load_app()["labels"]`; Snowflake labels from `PIPELINE_RESULTS.label_duplicate` (added to the `snowflake_rows` SELECT; missing -> info banner, no crash).
- MLRO page: `render_evidence_chain` (6 ordered expanders); STR rendered via `report_templates.render(draft, jurisdiction)` when importable (radio Generic / JURISDICTIONS), fallback `render_markdown`. Ownership path drawn as node -> relation -> node (`draw_path`).
- Risk head: exposure held by currency, median request->CASE_OPENED time from audit, analyst queue count.
- Robustness: Investigator "needs more evidence" list no longer assumes `investigation` is set (other agents now emit LOW results without one).
- Tests: +what-if (memory + fake Snowflake), evidence-chain steps, risk metrics; fake-session rows gained `LABEL_DUPLICATE`.
- Assumption: Snowflake `py_rules` holds fired rule ids (JSON array) and `label_duplicate` is a BOOLEAN column of CORE.PIPELINE_RESULTS.


## Round 4 (ITER-07 UI)

- Added `app/ui.py` (palette tokens, CSS, HTML card builders, formatters, status pills) and `.streamlit/config.toml` (light theme).
- Restyled all five pages to design v6: top bar + pill nav (`persona` radio moved from sidebar to main), stone/blue-grey
  background, white cards, Manrope/Source Serif 4 (web fonts only in memory mode). Case page rebuilt: short h1, fact line,
  single three-section card, Drafted report + All document details expanders, sticky decision card, Audit trail expander.
- Behaviour unchanged: DECIDE_CASE/decide() errors verbatim in st.error, Verify audit chain button, memory + Snowflake paths.
- Landing KPI "Detection · false positives" counts flagged (not Clear) vs ground-truth labels (matches PITCH numbers).
- Tests: smoke tests updated for new nav (`at.radio`), labels and headings; new tests for ui helpers. Suite green (408).

## Round 5 (taste-skill audit)

Scope: scan -> diagnose -> fix on the existing stack (no rewrite). Identity kept: Source Serif 4 + Manrope, "Midnight & apricot" tokens, short headings, no explanatory sentences, Bank A/B/C, no invented data.

Audit (problem -> fix), all in `app/ui.py` unless noted:
- Numbers in proportional figures -> `tabular-nums lining-nums` on body, tables, tiles, facts, chips, pills, nav, metrics.
- Orphaned words in headings -> `text-wrap: balance` (headings), `pretty` (sub/lead/notes).
- No tracking tuning -> -0.02em on h1/title, -0.03em on the big score, +0.02-0.04em on small labels and table headers.
- Shadows were neutral black-ish and inconsistent -> one navy-tinted family (rgba 36,55,94), light from above; subtle hover lift on tiles/metrics.
- Uniform radius -> tighter inside (callout/panel/node/report 10px, banner 8px, pills/chips 6px, logo 8px), softer containers (cards 18px).
- Pill "Synthetic data only"/"Memory backend" badges and status pills -> square flag-style badges with a left color edge; status pills 6px radius. Logo already a rounded square.
- Buttons/nav pills/selects/inputs lacked hover, pressed and focus states -> hover (primary darkens, secondary tints), `:active` translateY(1px) scale(.98), visible `:focus-visible` ring (accent outline + soft halo), 200ms transitions, reduced-motion respected.
- Nav showed only a white pill for current page -> adds an accent underline inset to the checked pill.
- Flat page -> very light pure-CSS grain (SVG feTurbulence data URI, opacity .035, fixed, pointer-events none).
- Spinner -> shimmer skeleton bar on `stSpinner` (cache-resource loading text kept).
- Alerts -> borderless with a left accent edge (inline error/empty states already used st.error/st.info with next-step wording).
- Dead link: "<- Back to cases" in `app/streamlit_app.py` was plain text -> now "Case N of M · ...".
- Favicon + page title: already `st.set_page_config(page_title="Suraksha", page_icon=shield)`, kept.
- Tests: new CSS/no-dead-link check in `tests/unit/test_app_smoke.py`.

Skipped (and why): picsum/stock imagery, GSAP/scroll/parallax, overlap/broken-grid layouts, text mask reveals (not a calm compliance tool; Snowflake has no external network); legal/cookie footers and custom 404 (not applicable); new fonts (user-approved identity); new packages; sidebar change (already top nav).
Compatibility: only plain CSS (`:has`, `:focus-visible` degrade harmlessly); no new Streamlit APIs; fonts still memory-mode only, system stacks in Snowflake.
