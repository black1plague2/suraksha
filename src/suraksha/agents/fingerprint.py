"""Fingerprint agent: salted-hash cargo fingerprints + consortium matching.

Keys (all sha256(salt + "|" + "|".join(parts))):
  exact : bl_norm | vessel | voyage | commodity | qty_band
  cargo : vessel | voyage | commodity | qty_band           (catches reissued B/L)
  bl    : bl_norm | vessel                                  (catches altered qty/commodity)
  blv   : bl_norm | voyage | commodity                      (vessel-independent: vessel typo/rename)
Extra, fingerprint-only keys "cargo_m1" / "cargo_p1" hold the cargo hash for band-1 / band+1
so `match` can probe neighbour bands without the raw values. They are stripped in `to_entry`
(the shared table keeps only exact/cargo/bl/blv).
"""
from __future__ import annotations

import hashlib
import math
import re

from suraksha.config import Settings
from suraksha.log import get_logger
from suraksha.models import (
    Citation,
    CitationKind,
    ConsortiumEntry,
    ConsortiumMatch,
    ExtractedFields,
    Fingerprint,
    FinancingRequest,
    MatchType,
)
from suraksha.store.base import Store

log = get_logger(__name__)

CORE_KEYS = ("exact", "cargo", "bl", "blv")
SIM_EXACT = 1.0
SIM_CARGO = 0.9
SIM_BL = 0.85
SIM_NEIGHBOUR = 0.75

# --------------------------------------------------------------------------- commodity canonicalisation
# Ordered (first match wins); patterns run on cleaned upper-case text.
_COMMODITY_RULES: list[tuple[str, str]] = [
    (r"\b(COLD ROLLED|CR)\b.*\b(STEEL|COILS?)\b|\bCRC\b", "STEEL_COILS_CR"),
    (r"\bSTEEL\b.*\bCOILS?\b|\bHRC\b|\bHR COILS?\b|\bHOT ROLLED\b.*\bCOILS?\b", "STEEL_COILS_HR"),
    (r"\bSTEEL\b.*\b(BILLETS?)\b|\bBILLETS?\b", "STEEL_BILLETS"),
    (r"\bSTEEL\b.*\b(REBARS?|BARS?)\b|\bREBARS?\b|\bTMT\b", "STEEL_REBAR"),
    (r"\bCOPPER\b.*\bCATHODES?\b|\bCATHODE COPPER\b|\bCU CATHODES?\b", "COPPER_CATHODES"),
    (r"\bALUMINI?UM\b.*\bINGOTS?\b|\bALUMINI?UM\b|\bAL INGOTS?\b", "ALUMINIUM_INGOTS"),
    (r"\b(CRUDE )?PALM OIL\b|\bCPO\b", "CRUDE_PALM_OIL"),
    (r"\bNICKEL\b", "NICKEL"),
    (r"\bSUGAR\b", "SUGAR"),
    (r"\bUREA\b", "UREA"),
    (r"\bCRUDE (PETROLEUM )?OIL\b", "CRUDE_OIL"),
    (r"\bIRON ORE\b", "IRON_ORE"),
    (r"\bTHERMAL COAL\b|\bCOAL\b", "COAL"),
    (r"\bWHEAT\b", "WHEAT"),
    (r"\bBASMATI\b|\bRICE\b", "RICE"),
    (r"\bSOY(A|ABEAN)? ?(BEANS?|MEAL)?\b", "SOYBEAN"),
    (r"\bZINC\b", "ZINC"),
    (r"\bDAP\b|\bDI ?AMMONIUM PHOSPHATE\b", "DAP_FERTILIZER"),
    (r"\bCOTTON\b", "COTTON"),
]
_COMMODITY_RE = [(re.compile(p), c) for p, c in _COMMODITY_RULES]


def canonical_commodity(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    t = re.sub(r"\([^)]*\)", " ", text.upper())          # drop "(Grade SS400)" style specs
    t = re.sub(r"[^A-Z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    for rx, canon in _COMMODITY_RE:
        if rx.search(t):
            return canon
    return t.replace(" ", "_")


# --------------------------------------------------------------------------- normalisation
def _norm_vessel(v: str | None) -> str | None:
    if not v:
        return None
    s = re.sub(r"\s+", " ", v.upper()).strip()
    s = re.sub(r"^(M\s*/\s*V|MV|M\.V\.|MT|M\s*/\s*T)\s+", "", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _alnum_upper(v: str | None) -> str | None:
    if not v:
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", v).upper()
    return s or None


def _norm_voyage(v: str | None) -> str | None:
    """Alnum upper; drop a leading V/VOY/VOYAGE tag before a digit and leading zeros ("V.066S" == "66S")."""
    s = _alnum_upper(v)
    if not s:
        return None
    s = re.sub(r"^(VOYAGE|VOY|V)(?=\d)", "", s)
    s = s.lstrip("0") or s
    return s


def band_of(qty_mt: float, width: float) -> int:
    """round(qty / width), half-up (avoids banker's rounding surprises)."""
    return int(math.floor(qty_mt / width + 0.5))


def normalize(fields: ExtractedFields, qty_band_mt: float = 50.0) -> dict[str, str]:
    """Canonical comparable parts. Only keys that could be derived are present."""
    out: dict[str, str] = {}
    bl = _alnum_upper(fields.bl_number)
    if bl:
        out["bl_norm"] = bl
    vessel = _norm_vessel(fields.vessel)
    if vessel:
        out["vessel"] = vessel
    voyage = _norm_voyage(fields.voyage)
    if voyage:
        out["voyage"] = voyage
    commodity = canonical_commodity(fields.commodity)
    if commodity:
        out["commodity"] = commodity
    if fields.quantity is not None:
        qty = fields.quantity
        if fields.quantity_unit and fields.quantity_unit.upper() != "MT":
            from suraksha.agents.intake import normalize_quantity
            qty, _ = normalize_quantity(qty, fields.quantity_unit)
        out["qty_band"] = str(band_of(qty, qty_band_mt))
    return out


def _h(salt: str, parts: list[str]) -> str:
    return hashlib.sha256((salt + "|" + "|".join(parts)).encode("utf-8")).hexdigest()


def borrower_token(reg_no: str, salt: str) -> str:
    return hashlib.sha256((salt + "|" + reg_no.strip().upper()).encode("utf-8")).hexdigest()


def _cargo_hash(salt: str, n: dict[str, str], band: int) -> str | None:
    if not all(k in n for k in ("vessel", "voyage", "commodity")):
        return None
    return _h(salt, [n["vessel"], n["voyage"], n["commodity"], str(band)])


def build_fingerprint(
    req: FinancingRequest,
    fields: ExtractedFields,
    borrower_reg_no: str,
    settings: Settings,
) -> Fingerprint:
    salt = settings.consortium_salt
    n = normalize(fields, settings.qty_band_mt)
    band = int(n["qty_band"]) if "qty_band" in n else None
    keys: dict[str, str] = {}
    skipped: dict[str, list[str]] = {}

    def need(key: str, required: list[str]) -> bool:
        miss = [r for r in required if r not in n]
        if miss:
            skipped[key] = miss
            return False
        return True

    if need("exact", ["bl_norm", "vessel", "voyage", "commodity", "qty_band"]):
        keys["exact"] = _h(salt, [n["bl_norm"], n["vessel"], n["voyage"], n["commodity"], n["qty_band"]])
    if need("cargo", ["vessel", "voyage", "commodity", "qty_band"]):
        keys["cargo"] = _cargo_hash(salt, n, band)  # type: ignore[arg-type]
        keys["cargo_m1"] = _cargo_hash(salt, n, band - 1)  # type: ignore[operator,arg-type]
        keys["cargo_p1"] = _cargo_hash(salt, n, band + 1)  # type: ignore[operator,arg-type]
    if need("bl", ["bl_norm", "vessel"]):
        keys["bl"] = _h(salt, [n["bl_norm"], n["vessel"]])
    if need("blv", ["bl_norm", "voyage", "commodity"]):
        keys["blv"] = _h(salt, ["blv", n["bl_norm"], n["voyage"], n["commodity"]])

    if skipped:
        log.warning("fingerprint_keys_skipped", extra={"ctx": {"request_id": req.request_id, "skipped": skipped}})
    log.info(
        "fingerprint_built",
        extra={"ctx": {"request_id": req.request_id, "bank_id": req.bank_id, "keys": sorted(keys), "qty_band": band}},
    )
    return Fingerprint(
        request_id=req.request_id,
        bank_id=req.bank_id,
        keys=keys,
        qty_band=band,
        borrower_token=borrower_token(borrower_reg_no, salt),
        created_at=req.submitted_at,
    )


def to_entry(fp: Fingerprint, entry_id: str | None = None) -> ConsortiumEntry:
    """Shared-table row: core keys only, no names, no amounts."""
    return ConsortiumEntry(
        entry_id=entry_id or f"CE-{fp.request_id}",
        bank_id=fp.bank_id,
        keys={k: v for k, v in fp.keys.items() if k in CORE_KEYS},
        qty_band=fp.qty_band,
        borrower_token=fp.borrower_token,
        pledged_at=fp.created_at,
    )


def match(fp: Fingerprint, store: Store) -> list[ConsortiumMatch]:
    """Find other-bank consortium entries matching `fp`. One match per entry (best), sorted desc."""
    core = {k: v for k, v in fp.keys.items() if k in CORE_KEYS}
    best: dict[str, ConsortiumMatch] = {}

    def offer(entry: ConsortiumEntry, mtype: MatchType, matched: list[str], sim: float) -> None:
        cur = best.get(entry.entry_id)
        if cur is not None and cur.similarity >= sim:
            return
        best[entry.entry_id] = ConsortiumMatch(
            request_id=fp.request_id,
            entry=entry,
            match_type=mtype,
            matched_keys=matched,
            similarity=sim,
            citation=Citation(
                CitationKind.CONSORTIUM, entry.entry_id, snippet="matched keys: " + ", ".join(matched)
            ),
        )

    if core:
        for e in store.find_consortium_by_keys(core, exclude_bank=fp.bank_id):
            equal = [k for k, v in core.items() if e.keys.get(k) == v]
            if "exact" in equal:
                offer(e, MatchType.EXACT, equal, SIM_EXACT)
            elif "cargo" in equal:
                offer(e, MatchType.FUZZY, equal, SIM_CARGO)
            elif "bl" in equal or "blv" in equal:
                offer(e, MatchType.FUZZY, equal, SIM_BL)

    neighbours = {fp.keys[k]: k for k in ("cargo_m1", "cargo_p1") if k in fp.keys}
    if neighbours:
        for e in store.find_consortium_by_band("cargo", list(neighbours), exclude_bank=fp.bank_id):
            which = neighbours.get(e.keys.get("cargo", ""))
            if which:
                offer(e, MatchType.FUZZY, ["cargo(neighbour_band)"], SIM_NEIGHBOUR)

    out = sorted(best.values(), key=lambda m: (-m.similarity, m.entry.entry_id))
    log.info(
        "consortium_match",
        extra={"ctx": {
            "request_id": fp.request_id,
            "bank_id": fp.bank_id,
            "n_matches": len(out),
            "matches": [(m.entry.entry_id, m.match_type.value, m.similarity) for m in out],
        }},
    )
    return out
