# Agent log — demo-slack (Haiku)

_Saved by master from the agent's hand-back (the agent did not write this file itself)._

## Built
- `scripts/demo.py` — narrated judge demo: `python scripts/demo.py [--seed 42] [--scenario dup_exact_shell] [--approve "Priya Nair"]`.
  Steps: 1 Intake · 2 Fingerprint & match (8-char hash prefixes only) · 3 Investigation · 4 Confidence · 5 STR · 6 Approval · 7 Audit trail + chain check.
  ASCII-only output (Windows console safe); sets SURAKSHA_LOG_LEVEL=WARNING before importing suraksha.
- `scripts/slack_server.py` — stdlib `http.server`, `POST /slack/actions`; verifies Slack v0 signature with
  SURAKSHA_SLACK_SIGNING_SECRET (401 if invalid/missing), parses `payload=`, calls `slack.handle_action`, returns
  `{ok, case_id, status, decided_by}`. `make_handler(app)` for tests. In-process state is lost on restart.
- `tests/unit/test_demo_and_slack_server.py` — 8 tests (demo default/scenario/approve/invalid; Slack integration,
  bad signature, missing secret, real-socket HTTP).

## Tests
`python -m pytest tests/unit/test_demo_and_slack_server.py -q` → **8 passed in 3.54s**

## Known gaps (master review)
- Demo shows registry ids (C0136, P0348) rather than names in steps 1 and 3 — polish in ITER-03.
- Ownership path rendering repeats the person node; should render as `C0136 <-UBO_OF- P0348 -UBO_OF-> C0134`.

## Polish (ITER-03)
**Completed:** All gaps resolved.

- **Registry names alongside IDs**: Added `fmt_entity()` helper that looks up company/person names via `store.get_company()` and `store.get_person()`. Borrower and counterparty now display as "Bharat Logistics LLP (C0136)", "Orient Exports Ltd (C0134)"; person names show as "Swati Bansal (P0348)"; shared UBOs and directors similarly resolved.

- **Ownership path rendering**: Implemented `fmt_ownership_path()` to chain edges starting from `from_company` and following connections. Edges stored with src→dst direction in model; display honors that direction for readability (person→company shown as `<-UBO_OF-`). Result: `Bharat Logistics LLP (C0136) <-UBO_OF- Swati Bansal (P0348) -UBO_OF-> Orient Exports Ltd (C0134)`.

- **"Why this matters" section**: Added step 8 showing two metrics: (1) **Amount at risk** — request currency and amount; (2) **Time to finding** — elapsed from first audit record (REQUEST_RECEIVED) to last (CASE_OPENED), combining request and case audit records, formatted as ms/s/m/h. Example: "USD 3,808,600" and "19ms".

- **Test assertions updated**: Both `test_demo_default_scenario()` and `test_demo_with_approval()` now verify names appear in output and ownership path contains "<-" or "->". Full test suite: `python -m pytest tests/unit/test_demo_and_slack_server.py -q` → **8 passed**.

- **ASCII-only**: All output remains ASCII-safe for Windows console (no emojis, Unicode arrows use ASCII "<-" and "->" only).
