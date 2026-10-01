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
