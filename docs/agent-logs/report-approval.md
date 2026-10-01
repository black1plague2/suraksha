# Agent log — report-approval (Sonnet)

_Saved by master from the agent's hand-back (the agent's Write call for this file was refused)._

## Built
- `src/suraksha/agents/report.py` — `draft_str` (FIU-IND PART 1–6), `validate_citations`, `render_markdown` ([^n] footnotes + citation list). Sentences lacking a citation are omitted, never emitted uncited.
- `src/suraksha/agents/approval.py` — `AuditLog` (sha256 chain, genesis `"0"*64`, seq from 1, `verify()` rechecks seq/prev_hash/entry_hash), `open_case` (idempotent, `CASE-<request_id>`), `decide` (refuses `system:*`/blank actors, REJECT needs reason, double decision → ValueError; actor stored as `officer:<name>`). Audit actions: CASE_OPENED; CASE_APPROVED + CASE_FILED + HOLD_RECOMMENDED; CASE_REJECTED.
- `src/suraksha/integrations/slack.py` — Block Kit approval message, `post_for_approval` (urllib, 5 s timeout, never raises), `handle_action`, `verify_signature` (Slack v0 HMAC-SHA256, constant-time compare, optional replay window).
- Tests: `tests/unit/test_report.py`, `test_approval.py`, `test_slack.py`.

## Decisions
- `draft_str(..., *, store=None, narrative_fn=None, now=None)` — extra kwargs; with `store`, ownership-path sentences show registry names.
- Request-level facts cite `Citation(REGISTRY, "requests:<request_id>")`.
- Shared persons cite `persons:<id>`, addresses `addresses:<id>`; shared phones cite an edge or R_SAME_PHONE citation, else the sentence is dropped.
- `build_approval_message` / `post_for_approval` accept optional `confidence`.

## Tests
`python -m pytest tests/unit/test_report.py tests/unit/test_approval.py tests/unit/test_slack.py -q` → **28 passed in 0.23s**

## Contract change requests
- Request-level citation convention → resolved by master: `registry` + `requests:<id>` is the documented convention (CONTRACTS.md).
- Pipeline should pass `store=` to `draft_str` → applied by master in ITER-01.

## Known gaps
- No HTTP endpoint wired for Slack interactivity; webhook messages carry no Reject-reason input.
- `AuditLog` lock is single-process; Snowflake needs DB-level sequencing.
- PART 3 lists at most 5 transactions.

## Round 2

Changes (only `agents/report.py`, `tests/unit/test_report.py`):
1. Policy clauses filtered by `select_policy_clauses` using module tables `RULE_POLICY_TAGS`, `ALWAYS_TAGS`, `KEYWORD_TAGS`. Fired rule -> tags; clause kept if its tags intersect wanted tags or it has str_filing/hold; related_party clauses only when an ownership/director/UBO/address/phone rule fired; re-issuance clauses (text/title "re-issu") only when match is FUZZY and matched_keys lack "exact"/"bl". Untagged clauses fall back to keyword matching.
2. `BANK_NAMES` + `reporting_entity_name()`: env SURAKSHA_REPORTING_ENTITY wins if set, else BANK_NAMES.get(bank_id, bank_id).
3. PART 5 begins with a cited "Summary:" sentence (matched bank, match type, strongest borrower link, score/band, action); narrative_fn text now goes second.
4. 7 new tests added.

Results:
- `python -m pytest tests -q`: 121 passed in 4.05s
- `python scripts/eval.py`: PASS G1_detection>=0.90, PASS G1_fpr<0.10, PASS G2_latency<5min, PASS G3_all_citations_valid, PASS G4_human_in_control+audit_chain

## Injection hardening (ITER-03)
- intake: free-text fields (commodity, shipper, consignee, vessel, ports) are clipped to the value proper (cut at first ". "+letter, ";", "{", "<", "|"; common abbreviations like "Pvt." kept), control/zero-width/bidi chars stripped, capped at 80 chars; full raw line stays in the citation snippet. New `detect_injection(text)` (ignore previous instructions, mark ... clear, approve this, system:, </document>, {"status", disregard ...) logs WARNING `intake_injection_detected` with request_id + doc_id. Schema unchanged.
- report: all document-derived values are quoted, markdown/HTML-escaped (`*_`[]<>|#`) and capped at 80 chars (e.g. `The pledged goods are described in the bill of lading as "Hot Rolled Steel Coils" (5,000 MT).`). Registry names stay unquoted.
- report: if any document trips `detect_injection`, PART 5 gets a DOCUMENT-cited sentence: "Document <doc_id> contains text resembling instructions to the reviewing system; it was treated as data and ignored."
- Tests added in tests/unit/test_intake.py and tests/unit/test_report.py.
