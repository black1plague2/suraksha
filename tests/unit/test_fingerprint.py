from datetime import datetime

from suraksha.agents.fingerprint import (
    borrower_token, build_fingerprint, canonical_commodity, match, normalize, to_entry,
)
from suraksha.agents.intake import extract
from suraksha.config import Settings
from suraksha.models import DocType, Document, FinancingRequest, MatchType
from suraksha.store.memory import MemoryStore

S = Settings(consortium_salt="test-salt", qty_band_mt=50.0)


def req(rid="R1", bank="BANK_A", bl="BL12345", vessel="M/V OCEAN STAR", voyage="045E",
        commodity="Hot Rolled Steel Coils", qty="5,000 MT", date="2026-03-15", t=datetime(2026, 6, 1)):
    text = (
        f"BILL OF LADING\nB/L No: {bl}\nShipper: S\nConsignee: C\nVessel: {vessel}\nVoyage No: {voyage}\n"
        f"Port of Loading: Mundra\nPort of Discharge: Jebel Ali\nDescription of Goods: {commodity}\n"
        f"Quantity: {qty}\nShipped on Board: {date}\n"
    )
    return FinancingRequest(rid, bank, "C1", 1.0, "USD", t,
                            [Document(f"{rid}-BL", rid, DocType.BILL_OF_LADING, text)])


def fp_of(r, reg="REG-1"):
    return build_fingerprint(r, extract(r), reg, S)


def pledge(store, r, reg="REG-1", eid=None):
    fp = fp_of(r, reg)
    store.add_consortium_entry(to_entry(fp, eid))
    return fp


def test_noise_gives_identical_exact_key():
    a = fp_of(req())
    b = fp_of(req(rid="R2", bank="BANK_B", bl="bl 12-345", vessel="MV  ocean   star",
                  commodity="HR STEEL COILS", qty="5,000,000 KG", date="15/03/2026"))
    assert a.keys["exact"] == b.keys["exact"]
    assert a.keys["cargo"] == b.keys["cargo"] and a.keys["bl"] == b.keys["bl"]


def test_commodity_canonicalisation():
    assert canonical_commodity("hot rolled steel coils") == "STEEL_COILS_HR"
    assert canonical_commodity("Cold Rolled Steel Coils") == "STEEL_COILS_CR"
    assert canonical_commodity("Copper Cathodes (Grade A)") == "COPPER_CATHODES"
    assert canonical_commodity("Aluminium Ingots") == "ALUMINIUM_INGOTS"
    assert canonical_commodity("Crude Palm Oil") == "CRUDE_PALM_OIL"
    assert canonical_commodity("Widgets & Gizmos") == "WIDGETS_GIZMOS"
    assert canonical_commodity(None) is None


def test_normalize_parts():
    n = normalize(extract(req(vessel="M/V Ocean  Star", bl="bl-123/4")), 50)
    assert n["bl_norm"] == "BL1234" and n["vessel"] == "OCEAN STAR"
    assert n["voyage"] == "045E" and n["commodity"] == "STEEL_COILS_HR" and n["qty_band"] == "100"


def test_hashes_do_not_leak_raw_values():
    fp = fp_of(req())
    blob = " ".join(fp.keys.values()) + fp.borrower_token
    for raw in ("BL12345", "OCEAN", "045E", "STEEL", "REG-1"):
        assert raw not in blob
    assert all(len(v) == 64 for v in fp.keys.values())


def test_borrower_token_deterministic_and_salted():
    assert borrower_token("REG-1", "s") == borrower_token("REG-1", "s")
    assert borrower_token("REG-1", "s") != borrower_token("REG-1", "t")
    assert borrower_token("REG-1", "s") != borrower_token("REG-2", "s")
    assert len(borrower_token("REG-1", "s")) == 64


def test_exact_match_across_banks():
    st = MemoryStore()
    pledge(st, req(), eid="E1")
    ms = match(fp_of(req(rid="R2", bank="BANK_B", qty="5,000,000 kg"), "REG-2"), st)
    assert len(ms) == 1
    m = ms[0]
    assert m.match_type == MatchType.EXACT and m.similarity == 1.0
    assert "exact" in m.matched_keys and m.entry.entry_id == "E1"
    assert m.citation.ref == "E1" and "matched keys" in m.citation.snippet


def test_reissued_bl_is_fuzzy_cargo_09():
    st = MemoryStore()
    pledge(st, req(), eid="E1")
    ms = match(fp_of(req(rid="R2", bank="BANK_B", bl="NEWBL999")), st)
    assert len(ms) == 1
    assert ms[0].match_type == MatchType.FUZZY and ms[0].similarity == 0.9
    assert ms[0].matched_keys == ["cargo"]


def test_same_band_rounding_matches():
    st = MemoryStore()
    pledge(st, req(qty="4,987.5 MT"), eid="E1")
    ms = match(fp_of(req(rid="R2", bank="BANK_B", qty="5,000 MT")), st)
    assert len(ms) == 1 and ms[0].similarity in (1.0, 0.9)


def test_adjacent_band_with_reissued_bl_is_neighbour_075():
    st = MemoryStore()
    pledge(st, req(qty="4,974 MT"), eid="E1")                                   # band 99
    fp = fp_of(req(rid="R2", bank="BANK_B", bl="OTHER1", qty="5,000 MT"))       # band 100
    assert fp.qty_band == 100
    ms = match(fp, st)
    assert len(ms) == 1
    assert ms[0].match_type == MatchType.FUZZY and ms[0].similarity == 0.75


def test_adjacent_band_same_bl_best_is_bl_085():
    st = MemoryStore()
    pledge(st, req(qty="4,974 MT"), eid="E1")
    ms = match(fp_of(req(rid="R2", bank="BANK_B", qty="5,000 MT")), st)
    assert len(ms) == 1 and ms[0].similarity == 0.85 and ms[0].matched_keys == ["bl"]


def test_two_bands_away_no_match():
    st = MemoryStore()
    pledge(st, req(qty="4,900 MT"), eid="E1")
    assert match(fp_of(req(rid="R2", bank="BANK_B", bl="OTHER1", qty="5,000 MT")), st) == []


def test_same_bank_excluded():
    st = MemoryStore()
    pledge(st, req(), eid="E1")
    assert match(fp_of(req(rid="R2", bank="BANK_A")), st) == []


def test_coloaded_different_commodity_no_match():
    st = MemoryStore()
    pledge(st, req(), eid="E1")
    other = req(rid="R2", bank="BANK_B", bl="ZZ9", commodity="Copper Cathodes")
    assert match(fp_of(other), st) == []


def test_one_match_per_entry_sorted_desc():
    st = MemoryStore()
    pledge(st, req(), eid="E1")                                                     # exact
    pledge(st, req(rid="R3", bank="BANK_C", bl="XX1"), eid="E2")                    # cargo
    pledge(st, req(rid="R4", bank="BANK_C", bl="XX2", qty="4,974 MT"), eid="E3")    # neighbour
    ms = match(fp_of(req(rid="R9", bank="BANK_B")), st)
    assert [m.entry.entry_id for m in ms] == ["E1", "E2", "E3"]
    assert [m.similarity for m in ms] == [1.0, 0.9, 0.75]


def test_missing_fields_skip_keys_not_hash_none():
    r = req()
    r.documents[0].text = "BILL OF LADING\nB/L No: BL1\nVessel: MV A\n"
    fp = fp_of(r)
    assert set(fp.keys) == {"bl"}
    assert fp.qty_band is None


def test_to_entry_core_keys_only_and_pledged_at():
    r = req()
    fp = fp_of(r)
    e = to_entry(fp)
    assert set(e.keys) == {"exact", "cargo", "bl"}
    assert e.pledged_at == r.submitted_at and e.borrower_token == fp.borrower_token
    assert e.entry_id == "CE-R1" and to_entry(fp, "X").entry_id == "X"
