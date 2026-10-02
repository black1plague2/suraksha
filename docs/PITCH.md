# Suraksha Pitch

## 30-Second Pitch

Suraksha catches trade-finance duplicate financing — when the same cargo is pledged to multiple lenders — before the money leaves the bank. Banks share only salted hashes and no customer names; Suraksha links cargo matches to ownership graphs and compliance policy, then hands a compliance officer a fully cited STR to approve. No case is filed without a named human. Nothing is black box.

## Glass-Box Positioning

**"Every alert can be defended in a regulatory audit without a data scientist in the room."**

Suraksha is not a machine-learning model that produces opaque scores. It is a deterministic rule engine with:

- **SQL-enforced human approval:** `DECIDE_CASE` in Snowflake SQL refuses system actors, blank reasons, and repeat decisions. No case is filed or hold recommended without a named officer's signature.
- **Citation on every sentence:** all 36 STRs have 0 uncited claims. Every sentence traces back to a document, registry row, policy clause, or consortium ledger entry.
- **Rule parity audit:** Python and SQL compute confidence scores deterministically. Blind audit of 45 cases showed 0 mismatches — the SQL rules reproduce Python exactly.
- **Hash-chained audit log:** the audit trail is append-only and cryptographically linked (each record commits to the previous). Auditors can detect tampering by replaying the chain.
- **No prompt injection:** documents are treated as untrusted data, quoted verbatim, never executed.

**Result:** A compliance officer, prosecutor, or auditor can open Snowsight, run the query, see the evidence, and defend the decision. No ML model, no hand-waving.

## 3-Minute Demo Script

### Setup (narrator: 15 seconds)
"Suraksha screens trade-finance requests for duplicate financing. Three synthetic banks have submitted 173 loan requests. We have one day's screening results. Let's walk through what happens when Suraksha detects a duplicate, and how the system ensures a human stays in control."

### Phase 1: Run the Batch (30 seconds, Snowsight)
**Narrator:** "First, the batch run. Click the Databases tab, select SURAKSHA, then the CORE schema. Run this query:"
```
CALL SURAKSHA.CORE.RUN_PIPELINE_BATCH(42, TRUE);
```
**What happens on screen:** A progress bar or result panel shows:
- 173 requests screened
- 100% detection (43/43 duplicate cases found)
- 1.5% false-positive rate (2 ambiguous cases, held for more evidence)
- 36 STRs drafted
- 14.3 seconds wall time
- Audit log: 567 records, chain intact

**Narrator:** "All 173 requests screened in 14 seconds. 43 duplicates found. None are filed yet. Every decision requires a named officer."

### Phase 2: Open the App (45 seconds, Streamlit)
**Narrator:** "Now, open the SURAKSHA_APP in Streamlit. This is where the compliance officer reviews flagged cases."
**What happens on screen:** A Streamlit dashboard shows:
- Personas selector (Analyst, Compliance Officer, Risk Head)
- A list of flagged cases: case ID, borrower name, confidence level (HIGH/LOW), status (PENDING_APPROVAL, NEED_MORE_EVIDENCE, CLEAR)
- When you click a case, it expands to show:
  - Extracted fields (B/L number, vessel, cargo, quantity, value, dates)
  - Fingerprint match (exact / fuzzy, similarity score)
  - Investigation findings (shared director, shared UBO, same address, etc.)
  - Fired rules and their weights
  - Confidence score (e.g., 0.75 / 1.0)
  - Evidence section: a list of citations (document page, registry row, consortium entry, policy clause)
  - The drafted STR text; every sentence is linked to an evidence row

**Narrator:** "For each case, the officer can see the entire chain of reasoning. Every claim is cited. There is nowhere to hide."

### Phase 3: Approval Decision (45 seconds, Streamlit or SQL)
**Narrator:** "Let's approve a case. Click on a HIGH-confidence case and select 'Approve'."
**What happens on screen (Streamlit):** A modal appears:
- "Approve this case?" with the case ID and a text field for "Approved by: [name]"
- The officer types their name (e.g., "Priya Nair")
- Clicks "Approve"

**Narrator:** "Or, the officer can approve via SQL. Let's do that instead."
**Switch to SQL (Snowsight):**
```
CALL SURAKSHA.CORE.DECIDE_CASE(
  'CASE-REQ-00016',
  'officer:Priya Nair',
  'APPROVE',
  'Verified borrower linkage via director; holds duplicate cargo pledge with Bank B.'
);
```
**What happens on screen:** Query succeeds:
- Case status changes to FILED
- hold_recommended set to TRUE
- decided_by = 'officer:Priya Nair'
- reason is recorded

**Narrator:** "The decision is logged, the case is filed, and a hold is recommended to Ops. Let's check the audit log to see the chain of decisions."

### Phase 4: Audit Log (15 seconds, Snowsight)
**Narrator:** "Click the AUDIT_LOG table. You'll see every action in order: the request was processed, confidence was scored, the STR was drafted, the case was opened, and finally approved."
**Show the query:**
```
SELECT * FROM SURAKSHA.CORE.AUDIT_LOG WHERE subject_id = 'CASE-REQ-00016' ORDER BY seq;
```
**What happens on screen:** Rows appear in order:
- `CASE_OPENED`, timestamp, officer name, subject_id, reason
- `CONFIDENCE_SCORED`, HIGH, ...
- `CASE_APPROVED`, officer:Priya Nair, reason
- `CASE_FILED`, ...
- `HOLD_RECOMMENDED`, ...

**Narrator:** "Each record includes a hash of the previous record. If any record is tampered with, the chain breaks. Auditors run a verification query to check:"
```
SELECT * FROM SURAKSHA.CORE.V_AUDIT_VERIFY_SUMMARY;
```
**Result:** Status = 'CLEAN', last_seq = 567, chain_intact = true.

**Narrator:** "The audit log is tamper-evident. Regulators can trust it."

### Phase 5: System Actor Refusal (15 seconds, Snowsight)
**Narrator:** "One more thing: the system enforces the human gate. Watch what happens when we try to approve as a bot:"
**Run:**
```
CALL SURAKSHA.CORE.DECIDE_CASE(
  'CASE-REQ-00017',
  'system:bot',
  'APPROVE',
  'Auto-approved by detector'
);
```
**Result on screen:** Error message:
```
SQL Error [90000]: decisions must be made by a named human officer, not a system actor
```

**Narrator:** "No matter how clever the automation, Snowflake SQL refuses to let a bot file a case. That is our guarantee."

### Closing (15 seconds, narration)
**Narrator:** "Suraksha detects duplicates in seconds, gathers all evidence, drafts a full STR, and hands it to a named human for approval. The audit log is immutable. Every alert can be defended. No black box. No surprises."

---

## Likely Judge Questions & Honest Answers

### Q1: "You're using synthetic data. How do you know it works on real data?"

**A:** Fair question. Our synthetic data includes 85 adversarial scenarios from a blind red-team (typos, unicode look-alikes, transshipment re-issues, split cargo, prompt injection). 100% recall, 0% false positives after fixes. The logic is commodity canonicalization, field normalization (regex), graph traversal (BFS), and deterministic scoring. Honest limit: both the generator and the detector were written by our team, so the seeded-set numbers are an upper bound; the blind red-team sets are the better signal, and they were later used to fix gaps. Real data would bring messier documents (scans, OCR) and registry gaps — Cortex AI_EXTRACT is wired for that but has not been tested on real scans.

### Q2: "One bank, three roles simulated. How does this scale to real multi-bank consortium?"

**A:** Our demo is a Snowflake single account with three bank roles (BANK_A_ROLE, BANK_B_ROLE, BANK_C_ROLE). Real multi-bank uses Snowflake Secure Data Sharing: each bank has its own account, publishes a share with only the hashes (no names, no amounts), and the consortium operator (or a neutral third party) unions the shares. We built the share infrastructure and test it (read [docs/CONTRACTS.md](CONTRACTS.md), `sql/02`). The share is one-way read-only. No bank can trick another bank's database. Scaling to 50 banks is operational (add 50 share subscriptions) and policy (consortium agreement). The bottleneck is real-time intake (one request per bank at a time), not matching.

### Q3: "Why no ML? You could train a fraud classifier."

**A:** We explicitly chose not to. STRs are regulatory filings; regulators audit them. A bank must be able to defend every claim in court without a data scientist explaining Shapley values or activation gradients. ML models are opaque; courts don't trust them in fraud cases. Suraksha uses deterministic rules (R_EXACT_HASH, R_SHARED_UBO, etc.) with explicit weights. A judge can read the rule, check the evidence, and sign off. That is the design choice.

### Q4: "What about false positives? You flagged two ambiguous cases."

**A:** Both were `decoy_hard_same_voyage_same_qty`: unrelated shippers, same vessel/voyage/commodity/quantity. Our confidence rules fired (fuzzy match + timing overlap), but the evidence was weak (no shared director, UBO, or address). We scored them as NEED_MORE_EVIDENCE, not APPROVED. The analyst is asked for more documents or registry lookups. No case is filed without the analyst's judgment. False positives are not a bug; they're a feature if handled correctly. The real failure is false *negatives* — missing a real duplicate. We achieved 100% recall on 43 seeded duplicates.

### Q5: "How do you handle timing pressure? Disbursement decisions are often made in hours."

**A:** Measured: the batch run screened 173 requests in 14.3 s on Snowflake (read 3.2 s, screen 1.1 s, write 10.0 s). The system gathers evidence and drafts the STR; a named officer decides — we do not auto-approve. Slack approval (signed interactive buttons) is implemented and unit-tested but was not part of the live demo. We have not measured officer review time.

### Q6: "The salt is shared. What if a bank brute-forces borrower identities?"

**A:** Valid threat. B/L numbers and reg numbers are low-entropy. A curious bank *can* brute-force the consortium if they know the salt. But: (a) the salt is kept out-of-band (not in version control, not in logs); (b) every query is logged and can be audited for anomalies; (c) consortium membership is a legal contract; (d) a bank doing this is violating the agreement and can be expelled. We document this threat in [docs/THREAT_MODEL.md](THREAT_MODEL.md). The defense is operational (audit + legal), not cryptographic perfection. If the consortium grows, we can add per-bank keys or secure multi-party computation.

### Q7: "The demo shows only one approval. What if the officer rejects?"

**A:** Same DECIDE_CASE flow. If the reason field is blank, SQL rejects it. If the officer says "insufficient evidence" or "borrower's explanation is credible," the case goes to CLOSED with a reason. The audit log records the rejection and reason. Regulators can see why the hold was not recommended. [TEST_coco.md](evidence/TEST_coco.md) shows both approval and rejection tests passing.

### Q8: "What if Snowflake has a bug or is compromised?"

**A:** We trust Snowflake's encryption at rest, encryption in transit, and access control. If Snowflake is fully compromised, all bets are off. But if Suraksha runs on-premise (Spark + PostgreSQL), the same code applies; we've architected around it (Store abstraction: MemoryStore, SnowflakeStore, PostgresStore). For a Snowflake-only demo, we assume Snowflake.

---

## Metrics That Matter to Banks

Measured in this project (synthetic data, seed 42, live on Snowflake):
- **Screening time:** 173 requests in 14.3 s (batch); in-memory screening step 1.1 s.
- **Detection / false positives:** 100% (43/43) / 1.5% (2/130, both ambiguous decoys, held for more evidence, not filed).
- **Explainability:** 36 STRs, 0 uncited sentences; Python and SQL rule scores identical (0 mismatches / 45 compared).
- **Control:** system actors, reason-less rejections and double decisions refused in SQL; app role cannot edit cases or audit rows.

What a bank would track in a pilot (not measured here — shown live in the Risk-head view where data exists):
- **Exposure held:** sum of amounts on cases pending or filed, by currency.
- **Time from request to drafted finding / to hold decision:** from audit-log timestamps.
- **Analyst queue:** NEED_MORE_EVIDENCE cases and their age.
- **Days of exposure avoided and analyst hours saved:** need a baseline from the bank's current manual process.

---

## [If Built: Near-Real-Time Submit → Flagged Within a Minute]

Suraksha currently processes batches or single requests. For a live intake flow (request arrives by email + PDF):
1. Intake agent extracts fields from PDF (5–10 s via Cortex AI_EXTRACT)
2. Fingerprint built and consortium queried (0.3 s)
3. If match, investigator walks registry graph (3–5 s)
4. Confidence scored and STR drafted (1–2 s)
5. Slack alert sent to officer with one-click approval link (instant)
6. Officer approves in Slack (0–2 min)

**Total:** submit → alert in 10–20 s; alert → decision in 2–5 min. This requires Cortex integration (live, not local regex) and Slack webhook (already wired in `src/integrations/slack.py`).

---

## [If Built: What-If Simulator]

A compliance officer might ask: "What if this borrower had used a different shell company name? Would we still catch it?"

**What-If Simulator (proposed):**
1. Officer enters a hypothetical scenario (e.g., borrower A + borrower B renamed, quantity rounded)
2. Suraksha re-scores the case with modified inputs
3. Shows the result: still HIGH confidence, or drops to LOW?
4. Visualizes which rules fire or fall away

**Benefit:** trains analysts on edge cases; helps set policy thresholds (e.g., "if UBO is shared but no direct link, is 0.30 confidence enough to hold?").

---

## [If Built: UAE goAML STR Export]

Suraksha currently drafts STRs in FIU-IND (India) format. A multi-jurisdictional consortium (India, UAE, Singapore) would need:
- **FIU-IND format** (done)
- **UAE goAML XML format** (template + mappings)
- **Singapore AMLC format** (template + mappings)

Each format has different fields (parties, amounts, risk indicators). The pipeline would:
1. Score the case in Suraksha (jurisdiction-agnostic)
2. Render the STR in the local regulatory format
3. Export as XML for filing

**Effort:** ~1 day per jurisdiction; we've factored the ReportSection / ReportSentence abstractions to support it.

---

## Summary

Suraksha is a **glass-box duplicate-financing detector** that:
- Detects with 100% recall and 1.5% false-positive rate
- Screens 173 requests in ~14 s on Snowflake, end to end
- Drafts fully cited STRs in compliance-ready format
- Enforces human approval in Snowflake SQL (no bypass)
- Logs every decision in a tamper-evident chain
- Defends every finding in a regulatory audit without ML hand-waving

**Banks care about:** speed (catch before disbursement), accuracy (no false holds), compliance (regulators trust it), and efficiency (analysts scale). Suraksha delivers all four.
