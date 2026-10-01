# synth-data agent log

## Built
- `src/suraksha/synth/generator.py` - `generate(seed, n_clean, n_dup, n_decoy)`, `load_into`, `SynthDataset`, registry builder, scenario builders, transactions.
- `src/suraksha/synth/templates.py` - static pools (commodities, vessels, ports, names, 11 policy clauses), `Noise`, `DocData`, `render_documents` (BL/INVOICE/LC/WR per CONTRACTS section A).
- `src/suraksha/synth/__init__.py` - exports generate, load_into, SynthDataset.
- `tests/unit/test_synth.py` - 10 tests.

## Seed 42 defaults (60/30/20): 173 requests, 234 companies, 607 persons, 3908 transactions
| scenario | count | label |
|---|---|---|
| clean | 60 | False |
| dup_original_* (5 each x 6) | 30 | False |
| dup_exact_same_borrower | 8 | True |
| dup_exact_shell | 6 | True |
| dup_reissued_bl | 7 | True |
| dup_rounded_qty | 9 | True |
| dup_format_noise | 6 | True |
| dup_weak_unlinked | 7 | True |
| decoy_anchor (base request of each decoy case) | 20 | False |
| decoy_coloaded | 5 | False |
| decoy_related_diff_cargo | 5 | False |
| decoy_same_commodity_diff_voyage | 4 | False |
| decoy_same_bank_refinance | 4 | False |
| decoy_hard_same_voyage_same_qty | 2 | False |

## Design decisions
- Dup cases are spread evenly over the 6 scenarios; each has 1 (65%) or 2 (35%) duplicates, each at a distinct bank, 1-40 days after the original.
- Linked borrowers are new companies built via recipes (shared UBO / director / address+phone / holding company / 3-hop holding chain / UBO via holding / combos), always <=3 graph hops (edges). Address/phone links are always combined with another link so confidence rules can reach HIGH. Weak-unlinked borrowers are fresh isolated companies (verified no path <=4 hops).
- Docs of exact/shell duplicates keep the ORIGINAL shipper on BL/invoice/LC (identical docs); only WR "Depositor" carries the new borrower name. Reissued/weak: new B/L and invoice number. Rounded: qty moved to nearest 50 or 100 MT (<=1 band), original offset from band centre <=22 MT so never on the x.5 band edge. Format noise: KG + commas, DD/MM/YYYY, M/V or MV, upper/lower case, double spaces, alias labels. ~20% of other requests get mild noise (aliases, M/V, DD/MM, commas, TONNES; never KG/case).
- Decoy cases need a base request: scenario `decoy_anchor` (label False). Not in contract; harmless extra scenario name. n_decoy counts the decoy requests themselves (anchors are extra). Hard decoys = min(2, n_decoy//8), qty differs by 4-10 MT within the same band, different shipper, different B/L.
- Background: max(100, 1.7*n_clean) unrelated companies, plus 5 small legit holding+2 subs groups, 3 shared business-centre addresses, 4 shared nominee directors. Dup/decoy borrowers are never drawn from the noise pool.
- Transactions: 14-30 rows per (borrower, bank), all dated 2025-09-01..2026-05-30 (before any request); linked dup borrower pairs get 2-4 round-trip rounds (same round amounts there and back within 1-4 days).
- Standard library only; per-request doc rendering uses `Random(f"{seed}:{request_id}")`.

## Tests
`python -m pytest tests/unit/test_synth.py -q` -> `10 passed in 0.30s`

## Known gaps
- Hard decoys and weak_unlinked are intentionally near-indistinguishable on cargo keys (differ only in qty delta / shipper text); expect FPs.
- Commodity text is always the canonical name (noise limited to case/spacing), so fingerprint canonical maps are not stressed by synonyms.
- Banks have no per-bank private noise beyond transactions.
