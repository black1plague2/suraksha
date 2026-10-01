# Suraksha: CoCo CLI Usage Guide

Snowflake Cortex Code CLI (CoCo) is used to plan, build, run, and test the duplicate-financing detector.

## Overview

CoCo is an AI coding agent for Snowflake. It explores your database, writes and runs SQL, builds Streamlit apps, and orchestrates workflows via terminal prompts. This guide shows exactly how the Suraksha team uses CoCo in each phase of development.

## PLAN Phase

**Goal:** Validate PRD and design schemas.

**Prompts:**

1. **Review PRD and data model**
   ```
   cortex
   > Summarize the Suraksha PRD (at docs/PRD.md). What are the core entities, 
   > detection rules, and audit requirements?
   ```
   **Artifact:** PRD summary + data model questions. **Evidence:** Screenshot of CoCo's response.

2. **Design consortium table**
   ```
   > Read sql/02_consortium_share.sql. Explain what fields are shared vs private, 
   > and why only hashes (not names) are safe for the consortium view.
   ```
   **Artifact:** Design approval (consortium isolation, fingerprint strategy). **Evidence:** Screenshot.

3. **Verify confidence rules**
   ```
   > Examine config.py and config.RULE_WEIGHTS. For each rule (R_EXACT_HASH, 
   > R_SHARED_UBO, etc.), confirm its weight aligns with the PRD detection strategy.
   ```
   **Artifact:** Rules checklist (all rules present, weights reasonable). **Evidence:** Screenshot.

## BUILD Phase

**Goal:** Create SQL, generate synthetic data, build Streamlit app, wire Cortex AI extraction.

**Prompts and order:**

1. **Create warehouse and schemas**
   ```
   > Using Snowflake syntax: design sql/00_setup.sql to create database SURAKSHA, 
   > schemas CORE, REGISTRY, CONSORTIUM, BANK_A, BANK_B, BANK_C, and roles 
   > (CONSORTIUM_ADMIN, BANK_A_ROLE, etc.) with the minimum grants needed.
   ```
   **Artifact:** `sql/00_setup.sql`. **Run:** `snowflake-connector` prompt to execute, or manual SQL in UI.

2. **Create core tables**
   ```
   > Write sql/01_tables.sql. Create tables in SURAKSHA.CORE for companies, 
   > persons, roles, corp_owners, addresses, transactions, policy_clauses; 
   > in SURAKSHA.REGISTRY for syndicated requests and extracted fields; 
   > in each SURAKSHA.BANK_* for private financing requests and documents. 
   > Use VARIANT for JSON keys (fingerprints) and payload (audit details).
   ```
   **Artifact:** `sql/01_tables.sql`. **Run:** Execute.

3. **Secure data sharing view**
   ```
   > Write sql/02_consortium_share.sql. Create a view in SURAKSHA.CONSORTIUM 
   > that exposes entry_id, bank_id, qty_band, borrower_token (hash), 
   > pledged_at, and keys (salted hashes). No names, amounts, or identifying data. 
   > Add a stored proc for banks to insert their entries into this shared view 
   > (they call it; Suraksha reads only).
   ```
   **Artifact:** `sql/02_consortium_share.sql`. **Run:** Execute, test secure sharing setup.

4. **Audit and immutability**
   ```
   > Write sql/03_audit_immutability.sql. Create an AUDIT table with fields 
   > seq, at, actor, action, subject_id, payload, prev_hash, entry_hash. 
   > Make it append-only: roles can INSERT but never UPDATE/DELETE. 
   > Add a view VERIFY_CHAIN that recomputes hashes and flags any tampering.
   ```
   **Artifact:** `sql/03_audit_immutability.sql`. **Run:** Execute.

5. **Confidence rules in SQL**
   ```
   > Write sql/04_rules.sql. Create views for each rule (R_EXACT_HASH, R_FUZZY_MATCH, 
   > R_SHARED_UBO, etc.) that join CONSORTIUM, REGISTRY, and TRANSACTIONS to 
   > compute scores matching config.RULE_WEIGHTS. Test each rule on sample data.
   ```
   **Artifact:** `sql/04_rules.sql` (views + sample queries). **Run:** Execute, verify outputs.

6. **Cortex AI staging**
   ```
   > Write sql/05_cortex.sql. Create a stage for uploaded PDFs, and Cortex 
   > functions for AI_EXTRACT (pull B/L fields from documents) and AI_COMPLETE 
   > (draft narrative sections for STRs). Include example calls.
   ```
   **Artifact:** `sql/05_cortex.sql`. **Run:** Execute.

7. **Generate synthetic data**
   ```
   > You have scripts/load_synth_to_snowflake.py. It should call suraksha.synth.generate() 
   > to create 60 clean cases, 30 duplicates (dup_exact_same_borrower, dup_exact_shell, 
   > dup_reissued_bl, etc.), and 20 decoys. Upload to SURAKSHA.CORE and SURAKSHA.BANK_A/B/C 
   > in order (registry first, then requests per bank). Show the insert counts.
   ```
   **Artifact:** `scripts/load_synth_to_snowflake.py` (or enhanced). **Run:** `python scripts/load_synth_to_snowflake.py`.

8. **Streamlit app skeleton**
   ```
   > Write app/streamlit_app.py. A Streamlit-in-Snowflake app with tabs for: 
   > Browse Consortium (show hashes, qty_band, pledge dates), 
   > Check Request (paste a new request, run intake → fingerprint → match), 
   > Approve Case (list pending cases, approve/reject with reason, show audit log), 
   > Query Registry (search by company name, show ownership graph).
   ```
   **Artifact:** `app/streamlit_app.py`. **Run:** Deploy to Snowflake.

## RUN Phase

**Goal:** Execute a full request end-to-end and show judges the workflow.

**Prompts:**

1. **Intake and extract**
   ```
   cortex
   > Load a bill of lading and invoice from the synthetic test set. 
   > Use sql/05_cortex.sql AI_EXTRACT to pull vessel, voyage, commodity, quantity, dates.
   > Show the extracted fields and their sources (document citations).
   ```
   **Artifact:** Extracted fields with citations. **Evidence:** Screenshot (extracted JSON + source snippets).

2. **Fingerprint and match**
   ```
   > Hash the extracted fields using the consortium salt. Look up the exact hash 
   > in sql/02_consortium_share.sql. Report any matches: which bank pledged this cargo before?
   ```
   **Artifact:** Match list (EXACT or FUZZY, with entry_id and similarity). **Evidence:** Screenshot (consortium entry details).

3. **Investigate and score**
   ```
   > For the matched entry, query sql/04_rules.sql to score ownership links 
   > (shared directors, UBO, address, phone, timing). Show which rules fired 
   > and their weights. Is the score above the confidence threshold (0.6)?
   ```
   **Artifact:** Confidence result (score, band HIGH/LOW, evidence list). **Evidence:** Screenshot (rules fired with weights).

4. **Draft STR**
   ```
   > Use sql/05_cortex.sql AI_COMPLETE to draft the STR narrative 
   > (FIU-IND structure: reporting entity, principal officer, transaction details, 
   > linked entities, grounds of suspicion). Every sentence must cite a document, 
   > registry row, policy clause, or rule.
   ```
   **Artifact:** STR draft (markdown, with [^n] citation footnotes). **Evidence:** Screenshot (STR + citations validated).

5. **Approve and audit**
   ```
   > Approve the case via Slack or the Streamlit app (officer name, signature). 
   > Query sql/03_audit_immutability.sql VERIFY_CHAIN to confirm the audit log 
   > is tamper-evident (hash chain intact, every action logged).
   ```
   **Artifact:** Case marked FILED, hold recommended, audit chain verified. **Evidence:** Screenshot (approval message + audit log).

## TEST Phase

**Goal:** Verify detection rate ≥90%, FPR <10%, <5 min end-to-end, all rules deterministic.

**Prompts:**

1. **Run evaluation**
   ```
   cortex
   > Execute scripts/eval.py. Load all 110 synthetic cases. 
   > For each: run intake → fingerprint → match → investigate → score → draft → audit. 
   > Report: detection rate (TP / (TP + FN)), FP rate (FP / (FP + TN)), 
   > mean elapsed_ms per request. All must match G1/G2 targets.
   ```
   **Artifact:** Eval report (metrics, per-case breakdown). **Evidence:** Log output screenshot (paste into analysis doc).

2. **Verify audit hash chain**
   ```
   > Query sql/03_audit_immutability.sql VERIFY_CHAIN on the full audit log. 
   > Check: every prev_hash points to the prior entry's entry_hash (unbroken chain), 
   > no out-of-order records, genesis record has prev_hash = '0'*64. Report any breaks.
   ```
   **Artifact:** Hash chain verification (all entries OK or list of breaks). **Evidence:** SQL query + result screenshot.

3. **Validate citations**
   ```
   > For all HIGH-confidence cases that drafted an STR: run validate_citations() 
   > (in agents/report.py). Confirm that every sentence in the STR has ≥1 citation 
   > and that the citation ref/page exists in the source data. Report count of errors.
   ```
   **Artifact:** Citation validation report (0 errors = success). **Evidence:** Pytest output screenshot.

4. **Compare Python rules to SQL rules**
   ```
   > For 10 random HIGH-confidence cases, manually compute the rule scores 
   > using Python (config.RULE_WEIGHTS + agents/confidence.py logic) and 
   > compare to the SQL view outputs (sql/04_rules.sql). They must be bit-identical.
   ```
   **Artifact:** Reconciliation report (Python vs SQL scores for 10 cases, all match). **Evidence:** Jupyter notebook or console output screenshot.

## Evidence Checklist for Judges

Capture these artifacts and screenshots as you run:

- [ ] **PLAN:** PRD summary (CoCo response), data model design, rules checklist
- [ ] **BUILD:** Screenshots of sql/00..05 schema creation, synthetic data load (row counts), Streamlit app deployed
- [ ] **RUN:** Extracted fields with citations, consortium match (entry details), confidence score (rules fired), STR draft (citations validated), approval + audit log
- [ ] **TEST:** Eval report (metrics: detection rate, FPR, latency), hash chain verification (no breaks), citation validation (0 errors), Python vs SQL rule comparison (10 cases match)

---

**Next Steps:** See [RUNBOOK.md](RUNBOOK.md) for local setup, Snowflake deployment, Slack integration, and troubleshooting.
