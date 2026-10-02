# Agent log — report-templates (Sonnet, ITER-06)

_Saved by master from the agent's hand-back (the agent's Write of this file was refused)._

## Built
- `src/suraksha/agents/report_templates.py`: `JURISDICTIONS = ("FIU-IND", "UAE-goAML")`, `render`, `to_structured`, `validate`
  (G3: no uncited sentence in any jurisdiction), `fired_rules`, `RED_FLAGS`. Unknown jurisdiction → ValueError.
- FIU-IND: `render` delegates to `report.render_markdown`; `to_structured` = the six PARTs.
- UAE-goAML: PART 1 → Reporting entity, 2 → Reporting person, 3 → Transaction(s), 4 → Involved parties, 5 → Reason for
  suspicion, 6 → Action taken; derived (all cited): Report details, Reasons/indicators, Attachments (one per cited document).
  `render` validates first and refuses to emit uncited content.
- Indicators only for rules that fired (detected from PART 5 "Rule R_X (weight…)" sentences); each cites the RULE citation plus
  the sentence's own citations. Red-flag table covers all 10 rule ids incl. R_NO_VESSEL_CALL, R_DOC_MISMATCH — descriptive names
  only, no invented goAML codes.
- `tests/unit/test_report_templates.py` (8 tests), `docs/REGULATORY_TEMPLATES.md`.

## Sources (honest status)
Official UAE FIU / CBUAE pages returned 403/404 to fetch; only search summaries were available:
- https://www.uaefiu.gov.ae/media/344kqvhd/goaml-web-report-submission-guide-v2-2-14-04-2021.pdf (surfaced, not read)
- https://rulebook.centralbank.ae/en/rulebook/34-how-submit-str-and-other-report-types (403)
- https://rulebook.centralbank.ae/en/rulebook/guidance-licensed-financial-institutions-suspicious-transaction-reporting (search only)
- https://uaefiu.gov.ae/en/stakeholders/reporting-entities/str-process/ (404)
- Cyprus FIU goAML schema description (generic goAML structure only)
Supported by summaries: goAML is the UAE FIU platform; STR (with transactions) vs SAR (without); STR covers parties,
transactions, reasons/red flags, action taken. Everything else is marked "(verify with UAE FIU goAML guidance)".
Output is markdown/JSON, not goAML XML.

## Tests
`tests/unit/test_report_templates.py` → 8 passed. (Full-suite failures at the time were in `test_app_smoke.py`, under concurrent
edit by the whatif-ui agent — not caused by this module.)
