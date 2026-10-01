# Suraksha Glossary

## Trade Finance Terms

**Bill of Lading (B/L)**
A document issued by a shipping carrier confirming that goods have been loaded onto a vessel. It serves as evidence of shipment and is pledged to banks as collateral for trade loans. A bill of lading is unique per shipment and includes vessel name, voyage number, cargo description, quantity, and ports.

**Letter of Credit (LC)**
A bank's written guarantee to pay a seller on behalf of a buyer, conditional on the presentation of shipping documents (B/L, invoice, etc.). Used to reduce credit risk in international trade.

**Reissued B/L**
A new bill of lading number issued for the same cargo, typically when there is a change in consignee or a correction. Suraksha treats reissued B/L as a potential duplicate financing risk: the same cargo may be pledged again at a different bank.

**Warehouse Receipt (WR)**
A document proving that goods are stored in a warehouse. Used in place of a bill of lading for non-shipped cargo (e.g., crude oil in storage). Functions as collateral similar to a B/L.

**Commercial Invoice**
A seller's bill to the buyer, showing commodity description, quantity, value, and B/L reference. Used to verify cargo details and value for loan disbursement.

**Port of Loading (POL) / Port of Discharge (POD)**
The ports where cargo is shipped and received. Matches in POL/POD are part of the fingerprint; a match suggests the same voyage.

**Commodity**
The goods being shipped (e.g., steel coils, crude palm oil, sugar). Canonicalized in Suraksha (e.g., "HR STEEL COILS" → "STEEL_COILS_HR") for consistent matching.

**Quantity Band**
A bucketing of cargo quantity (in metric tonnes) to enable fuzzy matching. Example: quantities 4,900–5,100 MT all fall into the same band if `QTY_BAND_MT=50`. Catches scenarios where quantity is rounded or mistyped during re-pledging.

**Duplicate Financing**
When the same cargo (or near-identical cargo) is pledged as collateral to multiple lenders simultaneously or in close succession, deceiving each lender into thinking the cargo is unique and available. Typical harm: borrower draws loans totaling more than the cargo is worth; when the loan matures, only one lender can take the cargo, the others suffer losses.

## Compliance & AML Terms

**Suspicious Transaction Report (STR)**
A formal report filed with financial intelligence units (e.g., FIU-IND in India) when a bank suspects money laundering, fraud, or other financial crimes. Suraksha drafts a structured STR (FIU-IND format) when duplicate financing is detected with high confidence.

**Financial Intelligence Unit - India (FIU-IND)**
India's financial intelligence authority. Banks operating in India must file STRs with FIU-IND within 7 days of detecting suspicious activity. Suraksha structures reports to FIU-IND standards.

**Money Laundering Reporting Officer (MLRO) / Principal Officer**
The compliance officer at a bank responsible for reviewing and approving STR filings. Suraksha requires a named human (MLRO or principal officer) to approve a case before it is filed; no system can auto-file.

**Ultimate Beneficial Owner (UBO)**
The natural person (human) who ultimately owns or controls a company, directly or indirectly through a chain of ownership. Identifying UBOs is key to linking shell companies. Suraksha walks the ownership graph to find shared UBOs.

**Related Party**
A person or entity with a relationship to a borrower (director, shareholder, spouse, UBO) that creates a conflict of interest or risk of collusion. Suraksha flags related-party links as evidence of borrower collusion.

**Shell Company**
A company with no real business operations, assets, or employees, typically created to hide ownership or enable fraud. Shell companies in trade finance often share directors, UBOs, or addresses with the primary borrower.

**Lender Due Diligence Failure**
When a bank does not verify that the cargo pledged to it is not already pledged elsewhere, creating the conditions for duplicate financing. Suraksha catches this by checking the consortium.

## Registry & Ownership Graph Terms

**Registry**
Suraksha's synthetic stand-in for corporate registries (MCA21 in India, OpenCorporates globally). Contains: companies, persons, roles (director, shareholder, UBO), corporate ownership links, addresses, phone numbers, transaction records, and policy clauses. In production, Suraksha would query live registries.

**Company ID**
A unique identifier for a company in the registry (e.g., "C0042"). All companies in Suraksha are synthetic.

**Person ID**
A unique identifier for an individual in the registry (e.g., "P0015"). Tracks natural persons across multiple company roles.

**Registration Number (Reg No)**
A company's official number in the corporate registry. Suraksha hashes this (with salt) to create the `borrower_token`, preventing the consortium from seeing company names.

**Director**
A person legally responsible for managing a company. Shared directors between two companies are a strong signal of collusion or common ownership.

**Shareholder**
A person or company with an ownership stake in another company. Tracked with percentage ownership (pct).

**Corporate Ownership (Owns relation)**
When one company directly or indirectly owns another. Suraksha walks this graph to find hidden ownership chains.

**Address**
A physical location (line, city, country). Companies and persons can share addresses; a match suggests a link (e.g., mail-order shells registered at the same address as the borrower).

**Graph Hop**
One step in the ownership graph (e.g., borrower → director → another company → UBO). Suraksha limits searches to `MAX_GRAPH_HOPS` (default 4) to control complexity.

## Fingerprinting & Matching Terms

**Fingerprint**
A deterministic hash of cargo identity. Suraksha computes three fingerprints per request:
- **exact**: hash(bl_norm | vessel | voyage | commodity | qty_band) — matches identical cargo
- **cargo**: hash(vessel | voyage | commodity | qty_band) — catches reissued B/L
- **bl**: hash(bl_norm | vessel) — catches altered quantities or commodity text

**Salted Hash**
A hash computed with a secret salt (e.g., `hash(salt | "|" | data)`). The consortium uses salted hashes so no two banks can collide without knowing the salt. If banks agree on a salt in advance (out-of-band), they can safely compare hashes without revealing data.

**Consortium Salt**
The shared secret used by all banks in the consortium to compute fingerprints. Set via `SURAKSHA_SALT` env var (default: "dev-only-salt-change-me"). Must be changed for production and kept confidential.

**Consortium Table (Shared View)**
A Snowflake secure view containing entries from all banks: entry_id, bank_id, keys (salted hashes), qty_band, borrower_token, pledged_at. No names, amounts, or identifying information. Banks insert via stored procedure; Suraksha queries to find matches.

**Consortium Entry**
One row in the shared consortium table: one bank's pledge of cargo. When a new loan request arrives, its fingerprint is checked against all existing entries from other banks.

**Consortium Match**
When a new request's fingerprint matches an existing consortium entry. Match type: EXACT (all keys match) or FUZZY (cargo or B/L key matches, or qty_band neighbor). Similarity: 1.0 (EXACT), 0.9 (same qty_band), 0.85 (B/L), 0.75 (neighbor band).

**Borrower Token**
A salted hash of a company's registration number. Stored in the consortium table instead of the company name. Used to link the borrower across requests without revealing their identity to other banks.

## Detection & Scoring Terms

**Rule Weight**
A score (0.0–1.0) assigned to each detection rule (e.g., R_EXACT_HASH = 0.50, R_SHARED_UBO = 0.30). Suraksha sums fired rule weights to produce an overall confidence score (capped at 1.0).

**Confidence Score**
A value (0.0–1.0) indicating how confident Suraksha is that a request represents duplicate financing. Computed as the sum of fired rule weights (capped at 1.0). If score ≥ CONFIDENCE_THRESHOLD (default 0.6), the case is HIGH confidence and an STR is drafted.

**Confidence Band**
HIGH or LOW. HIGH means score ≥ threshold → draft STR. LOW means score < threshold → ask analyst for more evidence.

**Evidence**
A rule that fired, with its weight, description, and citations. Example: rule_id="R_SHARED_DIRECTOR", description="Borrower A and borrower B share a director (Jane Doe)", weight=0.20, citations=[{kind: REGISTRY, ref: "persons:P0015", snippet: "Jane Doe, director of both C0042 and C0043"}].

**Citation**
A reference to the source of an assertion: DOCUMENT (e.g., doc_id, page, snippet from B/L), REGISTRY (e.g., company, person, role), CONSORTIUM (e.g., entry_id), POLICY (e.g., clause_id), TRANSACTION (e.g., txn_id), or RULE (e.g., R_EXACT_HASH).

**Timing Overlap**
When two pledges of the same (or similar) cargo occur within a time window (default `TIMING_WINDOW_DAYS=45`). Suspicious because it suggests the borrower is re-pledging cargo before the first loan matures.

## Suraksha System Terms

**Intake Agent**
Extracts structured fields (B/L number, vessel, voyage, commodity, quantity, dates, shipper, consignee) from unstructured documents (PDFs of B/L, invoice, etc.) using regex or Cortex AI_EXTRACT.

**Fingerprint Agent**
Normalizes extracted fields and builds fingerprints (exact, cargo, B/L hashes). Queries the consortium table for matches.

**Investigator Agent**
For HIGH-confidence matches, explores the ownership graph (BFS up to MAX_GRAPH_HOPS) to find shared directors, UBOs, addresses, and phone numbers. Gathers transaction history and policy clauses.

**Confidence Agent**
Scores the investigation using deterministic rules (R_EXACT_HASH, R_SHARED_UBO, etc.) with weights from config. Reports evidence and confidence band (HIGH/LOW).

**Report Agent**
Drafts an FIU-IND STR from the investigation, confidence, and extracted fields. Every sentence is cited.

**Approval Agent**
Manages case lifecycle: PENDING_APPROVAL → FILED (if approved) or CLOSED (if rejected). Requires a named human officer's signature.

**Audit Log**
An append-only record of all decisions, actions, and approvals. Each record is cryptographically linked to the prior record (hash chain) to detect tampering. Can be queried via SQL view VERIFY_CHAIN.

**Secure Data Sharing**
A Snowflake feature allowing one account to publish a view to other accounts without sharing raw data. Used here so banks can contribute to the consortium without revealing names or amounts.

**Streamlit-in-Snowflake**
Snowflake's hosted Streamlit application framework. Allows Python apps to run inside Snowflake and query databases directly without separate infrastructure.

**Cortex AI_EXTRACT**
Snowflake Cortex function for document understanding. Given a PDF and a prompt, returns structured data (e.g., B/L fields extracted from a PDF).

**Cortex AI_COMPLETE**
Snowflake Cortex function for text generation. Used to draft narrative sections of the STR (e.g., "Grounds of Suspicion") from investigation data.

**Disbursement Hold**
A recommendation by Suraksha to the bank to hold (temporarily delay) payment of a loan until the duplicate financing concern is resolved and approved by the MLRO.

---

## See Also

- [PRD.md](PRD.md) — Problem statement, goals, personas
- [ARCHITECTURE.md](ARCHITECTURE.md) — System design and module interactions
- [CONTRACTS.md](CONTRACTS.md) — Data shapes and module ownership
