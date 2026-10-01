# match-engine agent log

## Built
- `src/suraksha/agents/intake.py`: `extract`, `normalize_quantity`. Line-based, case-insensitive `Label: value` parser with alias lists per doc type; B/L authoritative, invoice for value/currency (fallback LC), fallbacks to invoice/LC/WR (WR "Ex Vessel: V / VOY" fills vessel/voyage). Every filled field gets a DOCUMENT citation (doc_id, page from `\f` split, snippet = matched line). Logs extracted/missing fields.
- `src/suraksha/agents/fingerprint.py`: `normalize`, `canonical_commodity`, `borrower_token`, `build_fingerprint`, `to_entry`, `match`.
- Tests: `tests/unit/test_intake.py`, `tests/unit/test_fingerprint.py`.

## Decisions
- `normalize(fields, qty_band_mt=50.0)` takes an optional band width (contract signature had none); `build_fingerprint` passes `settings.qty_band_mt`. Returns only derivable keys.
- Band = half-up `floor(q/width+0.5)` (not Python banker's round).
- `match` only has the Fingerprint (no raw parts), so `build_fingerprint` adds extra keys `cargo_m1` / `cargo_p1` (cargo hash for band-1 / band+1). `to_entry` strips them: the shared table keeps only exact/cargo/bl. Neighbour matching probes `store.find_consortium_by_band("cargo", [m1, p1])`.
- Similarity is best per entry: EXACT 1.0, cargo same band 0.9, bl 0.85, neighbour band 0.75. Consequence: if B/L number is unchanged and qty moves to an adjacent band, the `bl` key still fires -> 0.85 (not 0.75). 0.75 appears when the B/L was also reissued.
- Missing inputs skip the key (warning logged), never hash None.
- Vessel kept raw in ExtractedFields (e.g. "M/V OCEAN STAR"); stripped only in `normalize`. Voyage/B-L normalised to alnum upper. Borrower token upper-cases/strips reg_no.
- Commodity map is ordered regex rules (steel coils HR/CR, billets, rebar, copper, aluminium, palm oil, nickel, sugar, urea, crude oil, iron ore, coal, wheat, rice, soybean, zinc, DAP, cotton); unknown -> upper-snake of cleaned text. Bare "steel coils" -> STEEL_COILS_HR.

## Pytest
`30 passed in 0.17s` (`python -m pytest tests/unit/test_intake.py tests/unit/test_fingerprint.py -q`)

## Known gaps
- Single-line `Label: value` only; no table/OCR layouts. Multi-commodity or multi-vessel B/Ls take first match.
- Currency symbols / "crore"/"lakh" values not parsed.
- `match` relies on store returning candidates via keys; EXACT detection requires `exact` key present on both sides.

## Round 2 (red-team miss: dup_vessel_typo)
- New shared key `blv` = H(salt | "blv" | bl_norm | voyage | commodity); vessel-independent, in CORE_KEYS so `to_entry` shares it. Match: blv-only -> FUZZY 0.85 (same tier as bl). Entries lacking `blv` (old) just never match on it.
- Voyage normalisation (`_norm_voyage`): alnum upper, strip leading V/VOY/VOYAGE before a digit, strip leading zeros: "066S"="66S"="V.066S"="Voy 66S". This changes all voyage-derived hashes (exact/cargo/blv) vs round 1; old pledged entries with zero-padded voyages would no longer match (re-pledge/re-seed).
- Tests updated: normalize voyage "45E", adjacent-band case now matches ["bl","blv"], to_entry core keys include blv; added vessel-typo, Sea Falcon II decoy, same-bank, voyage variants, legacy entry w/o blv.
