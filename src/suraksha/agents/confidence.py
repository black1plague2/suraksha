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


_DOC_RULE_ASKS = {
    "R_NO_VESSEL_CALL": [
        "Obtain carrier confirmation of the vessel call and the original B/L; verify loading with the port authority.",
        "Arrange a physical inspection / collateral-manager confirmation that the cargo exists.",
    ],
    "R_DOC_MISMATCH": [
        "Ask the borrower to explain the document discrepancy and provide amended, consistent documents.",
        "Reconcile B/L, invoice, LC and warehouse receipt against the LC terms before any drawdown.",
    ],
}


def _doc_asks(rule_ids: set[str]) -> list[str]:
    return [a for rid in ("R_NO_VESSEL_CALL", "R_DOC_MISMATCH") if rid in rule_ids for a in _DOC_RULE_ASKS[rid]]


def score(inv: Investigation, settings: Settings, extra_evidence: list[Evidence] | None = None,
          extra_missing: list[str] | None = None) -> ConfidenceResult:
    """Consortium-match scoring. `extra_evidence` = evidence from investigator.document_checks (physical-cargo /
    cross-document rules); it is folded into the same weighted sum. `extra_missing` = investigator.document_gaps."""
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

    for e in extra_evidence or []:
        ev.append(Evidence(e.rule_id, e.description, w[e.rule_id], _dedupe(list(e.citations))))

    # 4 dp so Python and sql/04_rules.sql (exact decimals) agree at the threshold boundary
    total = round(min(1.0, sum(e.weight for e in ev)), 4)
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
        missing += _doc_asks(fired)
        missing += [g for g in (extra_missing or []) if g not in missing]
        if not missing:
            missing.append("Seek analyst review of the cargo documents for additional corroboration.")

    log.info("confidence_scored", extra={"ctx": {
        "request_id": inv.request_id, "rules": [e.rule_id for e in ev], "score": total, "band": band.value,
        "missing": len(missing)}})
    return ConfidenceResult(inv.request_id, total, band, ev, missing)


def score_standalone(evidence: list[Evidence], settings: Settings, *, request_id: str = "",
                     gaps: list[str] | None = None) -> ConfidenceResult:
    """Score document/cargo evidence when there is NO consortium match.

    The score is the sum of the fired rules' weights (4 dp), but the band is ALWAYS LOW: without a consortium
    match the system never auto-drafts an STR. The caller (pipeline) maps `score >=
    settings.standalone_review_threshold` to NEED_MORE_EVIDENCE and anything lower to CLEAR.
    """
    w = RULE_WEIGHTS
    ev = [Evidence(e.rule_id, e.description, w[e.rule_id], _dedupe(list(e.citations))) for e in evidence]
    total = round(min(1.0, sum(e.weight for e in ev)), 4)
    fired = {e.rule_id for e in ev}
    missing = _doc_asks(fired)
    if fired:
        missing.append("No consortium match: confirm with other consortium banks that this cargo has not been "
                       "pledged elsewhere under altered particulars.")
        missing += [g for g in (gaps or []) if g not in missing]
    log.info("confidence_standalone", extra={"ctx": {
        "request_id": request_id, "rules": sorted(fired), "score": total}})
    return ConfidenceResult(request_id, total, ConfidenceBand.LOW, ev, missing)
