# Threat Model

## Assets

1. **Customer identities** — bank account names, registration numbers, directors, ultimate beneficial owners
2. **Loan amounts** — value of each financing request, disbursement history, exposure by borrower
3. **Cargo identity** — bill of lading numbers, vessel names, voyage numbers, commodity descriptions, quantities, shipping routes
4. **Transaction metadata** — dates, timing windows, bank pairs involved, borrower relationships

## Actors and Threat Scenarios

### Honest Bank
- **Goal:** detect duplicate financing within own book; contribute to consortium safely
- **Access:** own loan requests + documents, salted hashes via consortium, registry queries
- **Trust model:** follows consortium protocol, approved by audit, logs all lookups

### Curious Member Bank
- **Goal:** learn whether the consortium salt allows them to brute-force or crack borrower identities or B/L numbers
- **Capability:** 
  - Can brute-force hashes offline if they guess a registration number or B/L (low-entropy space: ~10 million active companies in India, ~500k ports globally × vessels × voyage numbers)
  - Can observe timing: pledge timestamp in consortium reveals when a cargo was pledged at another bank
  - Can probe the borrower_token: hash(salt + "|" + reg_no); if they guess a competitor's customer, they can compute the token and query the consortium for matches
- **Weakness exploited:** B/L numbers, vessel names, and registration numbers are human-readable and low-entropy
- **Threat:** learn competitor's customer base, cargo patterns, or verify guesses about which customers use multiple banks

### Compromised Member Bank
- **Same as curious bank, but escalated:**
  - Can query the consortium table directly (as their bank role) to extract all borrower_tokens and search them against their own registry of known customer reg numbers
  - Can infer cargo identity from quantity_band + commodity; narrow the field by cross-referencing public shipping data
- **Threat:** reverse-engineer the consortium salt itself if given enough samples (not imminent; salt is 32–256 bits); extract the full customer base of other banks

### Outside Attacker
- **Access:** consortium share (if they breach network, DB, or obtain a share copy)
- **Capability:** same as compromised bank, but starting cold; no ability to query live (access control stops them)
- **Threat:** offline brute-force of the consortium dump

### Insider at Consortium Operator
- **Access:** source code, consortium salt, database credentials, audit logs, bank metadata
- **Capability:** everything; can manufacture false pledges, delete audit records, forge decisions
- **Threat:** undetectable manipulation of consortium state; cover-up of insider fraud; collusion with banks

## What Each Actor Can Learn from the Consortium Ledger

**Shared view (V_SHARED_LEDGER) contains:**
- `entry_id` — unique identifier per pledge
- `bank_id` — which bank made the pledge (not sensitive)
- `keys` — JSON of salted hashes (exact, cargo, bl, blv, bln)
- `qty_band` — binned quantity (e.g., "100" for 5000–5500 MT)
- `borrower_token` — salted hash of registration number
- `pledged_at` — timestamp

**A curious bank learns:**
1. **Borrower_token → customer base:** if they know a competitor's customer name, they can compute its registration number, hash it with the known salt, and confirm whether that customer uses the consortium. Over time (many samples), they learn which of their competitors' customers also borrow from other banks.
2. **Timing patterns:** the `pledged_at` timestamp reveals when a competitor pledged cargo; cross-referencing with shipping manifests (public) can narrow down which cargo.
3. **Cargo fingerprints:** the `exact`, `cargo`, `bl` keys encode vessel + voyage + commodity + quantity. A curious bank can:
   - Brute-force the vessel name: compare against Lloyd's shipping registry (~100k active vessels; feasible in minutes)
   - Brute-force the commodity: 20 canonical types; try all combinations of vessel + voyage + one commodity
   - Brute-force the B/L number: if they guess a shipper/consignee pair, compute all plausible B/L numbers for that pair, hash them, and query the consortium
4. **Quantity band:** not sensitive alone, but combined with vessel + commodity, narrows the cargo to a handful of shipments.

**An outsider without the salt cannot:**
- Compute borrower_tokens or cargo hashes (no salt)
- Query the consortium by hash (hashes are opaque to them)
- Reverse-engineer borrower identities or cargo (cryptographic hash)

## Real Weaknesses (and Why)

### Brute-Force Attack on B/L and Reg Numbers
**The weakness:** B/L numbers and registration numbers are low-entropy and often predictable (sequential, human-assigned). A party holding the salt can:
1. Enumerate plausible B/L numbers (e.g., all B/L issued by a shipper in a month)
2. For each, compute `hash(salt + "|" + bl_norm + "|" + vessel_name + ...)`
3. Check if any computed hash matches a key in the consortium ledger
4. If yes, they have confirmed the B/L was pledged at another bank

**Why this is hard to fully prevent:**
- B/L numbers are issued by shippers, not cryptographically random
- Vessel names are public (ship registries)
- Voyage numbers are human-assigned (often sequential)
- Quantity bands are rough (±50 MT per band); combined with commodity, they narrow significantly

**Impact:** A curious bank can probe the consortium for knowledge of specific customers or cargo, without needing direct access to competitor databases.

## Mitigations (Deployed)

### Keyed HMAC with Snowflake SECRET
- The consortium salt is stored as a **Snowflake SECRET** (encrypted in metadata, never in code or logs)
- Fingerprints are computed as `HMAC-SHA256(key=secret, msg=parts)` instead of plain `SHA256(salt + "|" + parts)`
- Requires `USE ROLE SURAKSHA_ADMIN` to read the secret; application roles cannot see it
- **Benefit:** Even if an insider steals the database snapshot, the hashes cannot be replayed without the secret

### Per-Period Key Rotation
- The consortium agrees to rotate the salt quarterly (or per-event, e.g., a known breach)
- A dual-hash overlap window (e.g., 2 weeks) allows old and new hashes to coexist during transition
- Banks re-hash their historic pledges during the overlap; older hashes are deprecated
- **Benefit:** Limits the damage window if the salt is ever exposed; new pledges use a fresh key unknown to attackers

### Rate-Limited Lookups via Procedure
- Member banks cannot run arbitrary `SELECT` on the ledger; they use **SP_PLEDGE_** procedures (owner's rights)
- Procedures are instrumented to log every lookup (bank_id, timestamp, query parameters)
- A future rate-limiting layer would reject >N lookups per bank per day
- **Benefit:** Detects a curious bank probing the consortium; audit trail is irrefutable

### Membership Agreements + Audit of Lookups
- Consortium membership is a legal contract: banks agree not to attempt brute-force or reverse-engineering
- The audit log records every query, approval, and decision; auditors can replay and detect anomalous patterns (e.g., Bank A querying borrower_tokens for 10,000 unknown reg numbers)
- **Benefit:** Legal deterrent; audit evidence for enforcement action

### Prompt Injection Protection (Treated as Data, Quoted)
- Documents (B/L, invoices) are treated as **untrusted data**
- The intake agent extracts fields using regex or Cortex AI_EXTRACT, but **all extracted text is quoted in citations**, never executed or interpolated
- Evidence sentences are built from templates; user-controlled text (document snippets) is embedded as string data, not code
- **Benefit:** A malicious PDF cannot inject SQL, shell commands, or alter reasoning

### Audit Log Tamper Evidence
- The audit log is **append-only** (no UPDATE, no DELETE for application roles)
- Each record includes a cryptographic hash of the previous record (`prev_hash`)
- Auditors can recompute the hash chain from record 1 to the latest; any missing or modified record breaks the chain
- **Benefit:** Tamper is detectable; an insider cannot cover up fraud without leaving forensic evidence

### Human Approval Enforcement in SQL
- The `DECIDE_CASE` stored procedure (owner's rights) enforces approval in the database, not just Python
- Checks:
  - `decided_by` must not be `system:*` (system actors cannot file cases)
  - `reason` must not be empty (rejections must be justified)
  - A case cannot be decided twice
- **Benefit:** Even if the Python app is compromised, SQL enforces the human gate; no bypassing

## Out of Scope (Future Work)

### Secure Multi-Party Computation (MPC)
- If a bank wants to query the consortium **without revealing its query**, MPC protocols (e.g., ObliviousRAM) can hide which pledges are fetched
- **Trade-off:** much higher complexity, latency, and coordination overhead; not justified for a single lookup per loan
- **Plan:** if the consortium grows to 50+ banks and queries are high-frequency, revisit

### Private Set Intersection (PSI)
- Instead of a shared hash table, banks could run PSI: "Do my pledges intersect with any other bank's pledges?" without revealing which ones
- **Trade-off:** PSI is one-way (you learn "yes/no" only); you cannot investigate the findings if they exist; requires secure channels and certified protocols
- **Plan:** future enhancement for banks seeking plausible deniability

### Hardware Security Modules (HSM)
- The Snowflake SECRET could be backed by an external HSM (e.g., Azure Key Vault, AWS CloudHSM)
- **Trade-off:** added operational complexity, latency, and cost; overkill for a trial
- **Plan:** production deployment would consider this

## Secondary Roles Pitfall

**Issue:** Snowflake's default `USE SECONDARY ROLES ALL` (newer accounts) causes a user with multiple roles to inherit all privileges from all roles, even if they explicitly `USE ROLE SURAKSHA_APP`.

**Example:** A human user is both `SURAKSHA_APP` and `ACCOUNTADMIN`. Even after `USE ROLE SURAKSHA_APP`, they retain `ACCOUNTADMIN` privileges (UPDATE, DELETE, CREATE). A privilege test will falsely pass.

**Mitigation deployed:** The runbook requires `USE SECONDARY ROLES NONE` before any privilege test. Verified in [TEST_coco.md](evidence/TEST_coco.md) Run 1 → Run 2.

**Lesson:** Privilege enforcement must account for role inheritance rules; single-account tests are brittle and require explicit secondary-role management.

---

## Summary

**Suraksha's security model is *not* cryptographic perfection.** B/L numbers and registration numbers are low-entropy; a party holding the salt can brute-force or dictionary-confirm a guessed identity. This is an inherent weakness of salted hashing on human-readable data.

**Suraksha's actual defense** is a multi-layer approach:
1. **Keyed HMAC + Snowflake SECRET** — hashes are hard to compute without the secret
2. **Key rotation** — limits exposure if the salt leaks
3. **Rate limiting + audit** — detects suspicious queries
4. **Legal/contractual** — membership agreements deter misuse
5. **Tamper-evident audit log** — insider fraud leaves forensic evidence
6. **SQL-level enforcement** — human approval cannot be bypassed even if Python is compromised
7. **Data vs. code** — documents are quoted, never executed

**Trade-offs accepted:**
- No MPC/PSI (too complex for single lookup per loan)
- Shared salt (simpler, operationally) vs. key-per-bank (more isolated, more complex)
- 45-day timing window (catches most re-pledging scenarios; some fast fraud escapes)

**Assumptions:**
- Consortium membership is governed by legal contract and audit
- Banks and the operator are incentivized not to defect (banking regulation + reputational risk)
- The salt is kept secret out-of-band (not in version control, not in logs)
- Snowflake's infrastructure (encryption at rest/in transit, access control) is trusted
