# red-team agent log

Independent adversarial set, written blind to detector internals (read only PRD, GLOSSARY, models, store, pipeline API, document templates).

Files: `tests/adversarial/cases.py` (16-company registry + 44 scenarios), `tests/adversarial/test_adversarial.py`, `scripts/redteam_eval.py` -> `logs/eval/redteam.json`.

## Results
- must_flag: 26/27 caught, recall 96.3%
- must_clear: 14/14 clear, FPR 0.0%
- either (3, unscored): dup_split_halves_diff_bl -> PENDING_APPROVAL; hard_same_voyage_same_qty_unrelated -> NEED_MORE_EVIDENCE; dup_missing_all_ids -> PENDING_APPROVAL
- pytest: 43 passed, 1 failed (no xfail used)

## Scenario families
Baselines (original pledge, clean multi-bank, same-bank re-presentation); classic duplicates (same borrower, related UBO, unrelated, 3-bank chain, shell 4 hops via corp_owners); text noise (vessel typo, M/V + case + spaces, voyage 066S/66S/V.066S, B/L spaces/dashes/compact, KG vs MT, TONNES, rounded qty, HRC synonym, commodity word order, DD/MM/YYYY, alias labels, Arabic shipper); structural (new B/L with same vessel/voyage, altered commodity or qty on same B/L, inflated invoice, split halves same/different B/L, 4-month late re-pledge, missing voyage, only 2 of 4 docs); decoys (co-loaded, partial shipment different qty, same commodity different voyage, same qty different vessel, sister vessel "Sea Falcon II", nominee director on 3 companies, nominee + shared vessel, holding-group sisters different cargo, shared phone only, prolific trader second cargo, WR-only unique).

## Misses
| scenario | expected | got |
|---|---|---|
| dup_vessel_typo ("Sea Falcon" -> "Sea Falkon", otherwise identical docs incl. same B/L, related UBO) | must_flag | CLEAR |

Hypothesis (from outside): vessel is hashed exactly after normalisation (case, M/V prefix, whitespace handled) with no edit-distance tolerance, so `exact` and `cargo` keys change. The `bl` key, which should still catch it, is apparently built from H(bl_norm | vessel) as documented, so the typo breaks that key too. Same B/L number plus identical commodity/qty/ports should have been enough for a fuzzy match; a B/L-number-only key, or fuzzy vessel matching (Levenshtein/phonetic) gated on matching B/L or voyage, would close it. Note this is the exact case the task brief called out.

## Observations (passes worth knowing)
- Unlinked exact duplicate lands in PENDING_APPROVAL, shell 4 hops lands in NEED_MORE_EVIDENCE (flagged, but not escalated despite the registry chain).
- "Sea Falcon II" decoy did not collapse into "Sea Falcon", so fuzzy vessel matching, if added, must stay B/L- or cargo-gated.
- `hard_same_voyage_same_qty_unrelated` yields NEED_MORE_EVIDENCE, a reasonable answer.
- Gaps in coverage: scenarios use a single registry; no adversarial text (injected instructions in documents), no missing-all-doc-types request, no currency conversion on value. These are the next things to try.
