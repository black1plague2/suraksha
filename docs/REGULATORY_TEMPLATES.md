# Regulatory templates

Module: `src/suraksha/agents/report_templates.py`. API: `JURISDICTIONS`, `render(draft, j)`,
`to_structured(draft, j)`, `validate(structured)`.

## How mapping works
The cited sentences from `report.draft_str` are the only source of claims. A jurisdiction template
re-maps them; it never adds an uncited claim (G3). `render()` for goAML raises `ValueError` if
`validate()` finds any uncited sentence.

| FIU-IND part | UAE-goAML section (descriptive name) |
|---|---|
| PART 1 Reporting entity | Reporting entity |
| PART 2 Principal officer | Reporting person / compliance officer |
| PART 3 Transaction details | Transaction(s) |
| PART 4 Linked individuals and entities | Involved parties (persons / entities) |
| PART 5 Grounds of suspicion | Reason for suspicion |
| PART 6 Action taken | Action taken |
| derived | Report details (type STR + recommended action, cites the action sentence) |
| derived | Reasons / indicators (fired rules only) |
| derived | Attachments (one line per cited source document, cites that document) |

Indicators: a rule counts as fired only if PART 5 holds a `Rule R_X (weight ...)` evidence sentence.
Each indicator sentence cites a RULE citation plus the evidence sentence's own citations.

## GCC / UAE red-flag mapping (Suraksha rule -> indicator description)
Descriptions are Suraksha's own wording of trade-based money laundering themes, not official goAML
indicator codes (verify with UAE FIU goAML guidance for the code list).

| Rule | Indicator |
|---|---|
| R_EXACT_HASH | Multiple financing against the same documents |
| R_FUZZY_MATCH | Near-duplicate / re-issued trade documents |
| R_SHARED_UBO / R_SHARED_DIRECTOR / R_CORP_OWNERSHIP / R_SAME_ADDRESS / R_SAME_PHONE | Related-party counterparties (common owner / director / ownership link / address / phone) |
| R_TIMING_OVERLAP | Same cargo financed in overlapping periods |
| R_NO_VESSEL_CALL | Shipment inconsistent with vessel movements |
| R_DOC_MISMATCH | Document inconsistencies |

## Sources consulted
Search only; the two official pages that were fetched returned 403/404, so nothing below was
read from the primary UAE FIU documents directly.
- https://www.uaefiu.gov.ae/media/344kqvhd/goaml-web-report-submission-guide-v2-2-14-04-2021.pdf (UAE FIU goAML web report submission guide; surfaced in search, not read in full)
- https://rulebook.centralbank.ae/en/rulebook/34-how-submit-str-and-other-report-types (CBUAE rulebook; 403 on fetch)
- https://rulebook.centralbank.ae/en/rulebook/guidance-licensed-financial-institutions-suspicious-transaction-reporting (CBUAE STR guidance; search result only)
- https://uaefiu.gov.ae/en/stakeholders/reporting-entities/str-process/ (404 on fetch)
- https://www.icpac.org.cy/zePortal/WebFiles/SELK/WebDocuments/Members/General%20Circulars/2015/11%202015/MOKAS_goAMLSchema_v4.0_Feb_2015_FINAL.pdf (another FIU's goAML schema description; generic goAML only)

## Verified vs to verify
Supported by search summaries of public sources:
- UAE FIU receives STRs via goAML (web form or XML).
- goAML distinguishes STR (transactions included) from SAR (no transactions).
- A report covers parties, transactions, reason/red flags, action taken, narrative; reason is mandatory.

To verify with UAE FIU goAML guidance:
- Exact section names, field layout, mandatory fields, and UAE-specific indicator code list.
- Whether UAE FIU reason codes/attachment rules match the descriptive sections used here.
- Whether trade-finance duplicate financing should be filed as STR vs other report types (e.g. UAE-specific types).
- Output is a markdown/JSON rendering, NOT a goAML XML file; no official field codes are used.

## Adding a jurisdiction
1. Add its name to `JURISDICTIONS`.
2. Add a mapping (part -> section) and a builder like `_goaml` returning the same dict shape.
3. Branch in `to_structured` and `render`; run `validate()` on the result before rendering.
4. Add tests: every sentence cited, structured keys, indicator list only fired rules.
