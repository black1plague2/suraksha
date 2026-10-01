"""Deterministic confidence scoring: every fired rule becomes cited Evidence."""
from __future__ import annotations

from suraksha.config import RULE_WEIGHTS, Settings
from suraksha.log import get_logger
from suraksha.models import (
    Citation,
    CitationKind,
    ConfidenceBand,
    ConfidenceResult,
    Evidence,
    GraphEdge,
    Investigation,
    MatchType,
)

log = get_logger(__name__)


def _rule(rule_id: str, snippet: str) -> Citation:
    return Citation(CitationKind.RULE, rule_id, snippet=snippet)


def _dedupe(cs: list[Citation]) -> list[Citation]:
    seen, out = set(), []
    for c in cs:
        k = (c.kind, c.ref)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _edges(inv: Investigation) -> list[GraphEdge]:
    return [e for p in inv.paths for e in p.edges]


def score(inv: Investigation, settings: Settings) -> ConfidenceResult:
    w = RULE_WEIGHTS
    ev: list[Evidence] = []
    edges = _edges(inv)
    m = inv.match
    same = inv.counterparty_company_id is not None and inv.counterparty_company_id == inv.borrower_id

    def add(rule_id: str, desc: str, cites: list[Citation]) -> None:
        ev.append(Evidence(rule_id, desc, w[rule_id], _dedupe([_rule(rule_id, desc)] + cites)))

    # --- cargo match strength
    if m.match_type == MatchType.EXACT:
        add("R_EXACT_HASH", "Identical cargo fingerprint already pledged at another bank "
            f"(matched keys: {', '.join(m.matched_keys)}).", [m.citation])
    else:
        add("R_FUZZY_MATCH", f"Near-duplicate cargo fingerprint at another bank (similarity {m.similarity:.2f}, "
            f"matched keys: {', '.join(m.matched_keys)}).", [m.citation])

    # --- identity / ownership
    if same:
        note = ("Same legal entity: the borrower is also the pledgor at the other bank "
                "(identical borrower token), the strongest possible link.")
        cites = [Citation(CitationKind.REGISTRY, f"companies:{inv.borrower_id}", snippet="same company id")]
        add("R_SHARED_UBO", note, cites)
        add("R_SHARED_DIRECTOR", note, cites)
    else:
        if inv.shared_ubos:
            cs = [Citation(CitationKind.REGISTRY, f"persons:{p}", snippet="shared UBO") for p in inv.shared_ubos]
            cs += [e.citation for e in edges if e.relation == "UBO_OF"]
            add("R_SHARED_UBO", f"Borrower and counterparty share UBO(s): {', '.join(inv.shared_ubos)}.", cs)
        if inv.shared_directors:
            cs = [Citation(CitationKind.REGISTRY, f"persons:{p}", snippet="shared director")
                  for p in inv.shared_directors]
            cs += [e.citation for e in edges if e.relation == "DIRECTOR_OF"]
            add("R_SHARED_DIRECTOR",
                f"Borrower and counterparty share director(s): {', '.join(inv.shared_directors)}.", cs)
        own_paths = [p for p in inv.paths if p.edges and all(e.relation == "OWNS" for e in p.edges)]
        if own_paths:
            p = own_paths[0]
            add("R_CORP_OWNERSHIP",
                f"Corporate ownership chain of {len(p.edges)} hop(s) links the two companies.",
                [e.citation for e in p.edges])
    if inv.shared_addresses:
        add("R_SAME_ADDRESS", f"Same registered address: {', '.join(inv.shared_addresses)}.",
            [Citation(CitationKind.REGISTRY, f"addresses:{a}", snippet="shared address") for a in inv.shared_addresses]
            + [e.citation for e in edges if e.relation == "REGISTERED_AT"])
    if inv.shared_phones:
        add("R_SAME_PHONE", f"Same contact phone: {', '.join(inv.shared_phones)}.",
            [Citation(CitationKind.REGISTRY, f"companies:{inv.borrower_id}", snippet="shared phone")]
            + [e.citation for e in edges if e.relation == "HAS_PHONE"])

    t = inv.timing_overlap_days
    if t is not None and t <= settings.timing_window_days:
        add("R_TIMING_OVERLAP", f"Second pledge {t} day(s) after the first, within the "
            f"{settings.timing_window_days}-day window.", [m.citation])

    total = round(min(1.0, sum(e.weight for e in ev)), 6)
    band = ConfidenceBand.HIGH if total >= settings.confidence_threshold else ConfidenceBand.LOW

    missing: list[str] = []
    if band == ConfidenceBand.LOW:
        fired = {e.rule_id for e in ev}
        if inv.counterparty_company_id is None:
            missing.append("Confirm counterparty identity with consortium bank "
                           f"{m.entry.bank_id} (borrower token not found in registry).")
        if m.match_type == MatchType.FUZZY:
            missing.append("Obtain original B/L from carrier to confirm reissue.")
        if not fired & {"R_SHARED_UBO", "R_SHARED_DIRECTOR", "R_CORP_OWNERSHIP"}:
            missing.append("Request UBO declaration from borrower.")
            missing.append("Obtain shareholder/director register for the borrower and counterparty (MCA21 check).")
        if "R_TIMING_OVERLAP" not in fired and inv.timing_overlap_days is not None:
            missing.append("Verify pledge and disbursement dates with the other bank "
                           f"(gap of {inv.timing_overlap_days} days exceeds {settings.timing_window_days}).")
        if not missing:
            missing.append("Seek analyst review of the cargo documents for additional corroboration.")

    log.info("confidence_scored", extra={"ctx": {
        "request_id": inv.request_id, "rules": [e.rule_id for e in ev], "score": total, "band": band.value,
        "missing": len(missing)}})
    return ConfidenceResult(inv.request_id, total, band, ev, missing)
