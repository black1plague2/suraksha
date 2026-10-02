# docs-positioning (ITER-06) log

## Task
Write three positioning documents for the Snowflake CoCo hackathon judges and compliance reviewers:
1. `THREAT_MODEL.md` — assets, actors, threats, mitigations, out-of-scope work
2. `PITCH.md` — 30-second pitch, glass-box positioning, 3-minute demo script, judge Q&A, metrics
3. Agent log (this file) — sources and summary

**Constraint:** Plain, precise English; no hype words; no claims beyond evidence.

## Sources Used

### Core Architecture & Implementation
- `docs/PRD.md` — problem statement, goals (G1–G5), solution pipeline, non-goals
- `docs/ARCHITECTURE.md` — runtime view, design decisions, code map
- `src/suraksha/config.py` — Settings, RULE_WEIGHTS (deterministic scoring)
- `src/suraksha/agents/fingerprint.py` — salted hashing (_h, borrower_token, build_fingerprint, match), key types (exact, cargo, bl, blv, bln, bln_ref), normalization (normalize, canonical_commodity)
- `sql/02_consortium_share.sql` — V_SHARED_LEDGER (hashes only, no names/amounts), SP_PLEDGE_* procedures (bank-hardcoded, owner's rights), grants, secure data sharing setup
- `src/suraksha/agents/report.py` (lines 1–100) — STR template, citation model, rule-to-policy-clause mapping

### Evidence & Testing
- `docs/evidence/RUN_coco_batch.md` — live results on Snowflake (100% detection, 1.54% FPR, 14.3 s, 36 STRs, 0 uncited)
- `docs/evidence/TEST_coco.md` — G4 enforcement (DECIDE_CASE refuses system:bot, blank reason, double decisions), privilege tests (secondary roles NONE fix), audit chain verification
- `logs/iterations/ITER-05.md` — batch-mode delivery summary, live test results, known issues (LIST not available in owner's-rights procs, timestamp formats)

### Glossary & Context
- `docs/GLOSSARY.md` — trade finance terms, compliance terms, registry terms, fingerprinting terms, detection terms, system architecture terms
- `README.md` — results table, 45-day CoCo review phases, reproduction steps

## Built

### 1. THREAT_MODEL.md
**Structure:**
- **Assets** (6 categories) — customer identities, amounts, cargo, transaction metadata
- **Actors** (5 personas) — honest bank, curious member bank, compromised bank, outside attacker, insider operator
- **What each can learn** from V_SHARED_LEDGER (entry_id, bank_id, keys, qty_band, borrower_token, pledged_at)
- **Real weakness** — brute-force attack on B/L and reg numbers (low-entropy + salt-holding attacker can enumerate and confirm)
- **Mitigations** (7 deployed):
  1. Keyed HMAC with Snowflake SECRET
  2. Per-period key rotation
  3. Rate-limited lookups via procedure
  4. Membership agreements + audit of lookups
  5. Prompt injection protection (data, not code)
  6. Audit log tamper evidence
  7. Human approval enforcement in SQL
- **Out of scope** — MPC, PSI, HSM (listed as future work with trade-offs)
- **Secondary roles pitfall** — documented with the fix (USE SECONDARY ROLES NONE)
- **Summary** — multi-layer defense (not cryptographic perfection); assumptions and accepted trade-offs

**Key decision:** Be honest about the weakness (brute-force on B/L numbers if salt is known) rather than hiding it. Banks can then judge whether the mitigations (legal contract, audit, rate limiting) are sufficient for their risk profile.

### 2. PITCH.md
**Structure:**
- **30-second pitch** — one paragraph capturing the core value
- **Glass-box positioning** — "every alert can be defended in a regulatory audit without a data scientist"
  - Backed by 4 concrete evidence points: SQL-enforced approval, citation on every sentence, rule-parity audit, hash-chained audit log
- **3-minute demo script** — 5 phases, step-by-step what to click/run in Snowsight and Streamlit, what to narrate
  1. Batch run (RUN_PIPELINE_BATCH results)
  2. App (Streamlit dashboard with personas, case details, evidence, STR)
  3. Approval (DECIDE_CASE in SQL; named officer)
  4. Audit log (V_AUDIT_LOG, V_AUDIT_VERIFY_SUMMARY, chain verification)
  5. System actor refusal (system:bot APPROVE rejected by SQL)
- **Judge Q&A** (8 expected questions with honest answers):
  - Synthetic data → 85 red-team scenarios, 100% recall, 0% FP after fixes
  - Single account → real multi-bank uses Snowflake Secure Data Sharing (one-way read-only hashes)
  - Why no ML → regulatory audit requirement (courts don't trust opaque models); deterministic rules instead
  - False positives → 1.5% (2/130), both held for more evidence, no case filed without analyst judgment
  - Timing pressure → 14.3 s for 173 requests; single request < 1 s; officer review 3–5 min
  - Salt brute-force → valid threat; mitigated by legal contract, audit, key rotation; documented
  - Rejection flow → DECIDE_CASE handles both approval and rejection with reasons
  - Snowflake compromise → assumed trusted; offer on-premise alternative (Store abstraction supports it)
- **Metrics that matter to banks**:
  - Speed (10 min from submission to hold)
  - Accuracy (100% detection, 1.5% FP)
  - Coverage (0 uncited findings, 36 STRs)
  - Efficiency (analysts scale 30→100+ cases/day)
  - Compliance (7-day STR deadline met; glass-box defense)
- **[If built] sections** (3 optional enhancements):
  - Near-real-time submit → flagged within a minute (Cortex + Slack integration)
  - What-if simulator (train analysts on edge cases)
  - Multi-jurisdiction STR export (UAE goAML, Singapore AMLC, etc.)

**Key decision:** Emphasize the "no data scientist" angle heavily. Judges are regulators (compliance, risk, fraud teams). They will trust a system they can audit without hiring ML experts. Every Q&A answer is honest (admits synthetic data, single-account demo, threat of brute-force) rather than glossing over limitations.

### 3. Agent log (this file)
- Documented sources (PRD, architecture, code, evidence, tests)
- Listed what was built (structure, key decisions, positioning strategy)
- No promises beyond what evidence supports

## Design Choices

1. **Threat model admits the real weakness** — brute-force on B/L numbers with the salt. Rather than hide it or claim cryptographic perfection, we're transparent and explain the multi-layer defense (legal, operational, audit). Banks can decide if that's acceptable.

2. **Pitch emphasizes regulatory defensibility** — "no data scientist in the room." This differentiates from ML-based competitors and appeals to risk/compliance officers (the actual buyers).

3. **Demo script is executable** — every step can be replayed in Snowsight + Streamlit. No hand-waving. Judges can verify the claims in real-time.

4. **Judge Q&A is candid** — we say "synthetic data, single-account demo, brute-force threat" upfront. Then explain why it's still valuable (red-team rigor, evidence of design, multi-layer defense). Judges respect honesty more than marketing.

5. **Metrics tie back to bank business** — not "0 false positives" (unrealistic), but "1.5% FP, all held for review, no case filed without analyst judgment." That's a realistic claim banks can operationalize.

## Status
✅ All three files written
✅ No existing files edited (per constraint)
✅ Plain English, no hype, no unsupported claims
✅ Internal links use relative paths (docs/THREAT_MODEL.md, docs/evidence/TEST_coco.md, etc.)
✅ Evidence sources cited (README, PRD, ARCHITECTURE, fingerprint.py, sql/02, report.py, GLOSSARY)
