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
