# Agent log: investigator

## Built
- `src/suraksha/agents/investigator.py`: `resolve_borrower`, `investigate`, plus helpers `build_graph`, `shortest_paths`, `holders`, `describe_edge`, private `_borrower_token` (sha256(salt + "|" + reg_no)).
- `src/suraksha/agents/confidence.py`: `score(inv, settings)`.
- `src/suraksha/agents/linkage.py`: `linked_entities`, `answer`, `find_companies`.
- Tests: `tests/unit/test_investigator.py`, `test_linkage.py`, `test_confidence.py`.

## Decisions
- Graph is built from the whole registry per call. Edges keep registry direction and relation (DIRECTOR_OF / UBO_OF / SHAREHOLDER_OF person->company, OWNS owner->owned, REGISTERED_AT, HAS_PHONE) and are traversed undirected. Within a path, an edge's src/dst may be reversed relative to walk order.
- Edge citations are REGISTRY: `roles:<cid>/<pid>/<role>`, `corp_owners:<owner>/<owned>`, `companies:<cid>`, each with a snippet.
- BFS gives up to 3 distinct (by node sequence) shortest paths within `max_graph_hops`. A pure-OWNS path is appended as an extra (4th) path if it was not already among the shortest, so R_CORP_OWNERSHIP is not missed.
- shared_directors / shared_ubos include roles held in a direct parent holding company (one corp_owners hop) on both sides.
- R_CORP_OWNERSHIP fires on any path made solely of OWNS edges (includes sibling companies under one holding company, a 2-hop OWNS path).
- Same borrower at both banks: `paths == []`; shared_* are the company's own roles/address/phone; confidence fires R_SHARED_UBO and R_SHARED_DIRECTOR with a RULE citation "Same legal entity ..." (and address/phone if present).
- Unresolved token: Investigation returned with counterparty None, no paths/shared; confidence asks "Confirm counterparty identity with consortium bank ...".
- Score rounded to 6 dp before threshold comparison to avoid float edge issues. Every Evidence has a RULE citation first, plus registry/consortium citations. Shared-person evidence cites `persons:<id>` (the Investigation does not carry role rows) plus matching path edges.
- LOW `missing` asks: B/L from carrier (FUZZY), UBO declaration + director/shareholder register (no ownership link), counterparty confirmation (unresolved), date verification (outside timing window).
- All decision points logged (counterparty resolved/unresolved with paths/shared sets, confidence rules/score/band, linkage intent).

## Pytest
`20 passed in 0.16s` (`python -m pytest tests/unit/test_investigator.py tests/unit/test_linkage.py tests/unit/test_confidence.py -q`)

## Known gaps
- Graph rebuilt on every `investigate`/`answer` call (O(companies x roles) on MemoryStore); fine for synthetic scale, cache if needed.
- `answer()` returns `Citation` objects (not JSON dicts) in `citations`; rows use readable edge strings in `via` for `answer`, while `linked_entities` returns GraphEdge lists.
- Company lookup is exact id / full name / name minus legal suffix; no typo tolerance.
- No timezone handling in timing_overlap_days (assumes both datetimes same awareness).
- Not tested against the real synth dataset or fingerprint module (built in parallel).

## Contract change requests
None.
