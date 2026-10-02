"""Report agent: drafts an STR in FIU-IND structure with a citation on every sentence.

Deterministic, template based. An optional ``narrative_fn`` (e.g. Cortex complete) may add a
narrative sentence to PART 5; that sentence inherits the citations of the evidence it was
generated from, so the "no uncited sentence" invariant still holds.
Rule: if a needed citation is missing, the sentence is omitted rather than emitted uncited.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from suraksha.config import Settings
from suraksha.agents.intake import detect_injection
from suraksha.log import get_logger
from suraksha.models import (
    Citation,
    CitationKind,
    ConfidenceResult,
    ExtractedFields,
    FinancingRequest,
    Investigation,
    ReportSection,
    ReportSentence,
    STRDraft,
)

log = get_logger(__name__)

RECOMMENDED_ACTION = "HOLD_DISBURSEMENT"

# Reporting entity follows req.bank_id unless SURAKSHA_REPORTING_ENTITY is explicitly set.
BANK_NAMES: dict[str, str] = {
    "BANK_A": "Bank A (synthetic)",
    "BANK_B": "Bank B (synthetic)",
    "BANK_C": "Bank C (synthetic)",
}


def reporting_entity_name(bank_id: str, settings: Settings) -> str:
    if os.environ.get("SURAKSHA_REPORTING_ENTITY"):
        return settings.reporting_entity
    return BANK_NAMES.get(bank_id, bank_id)


# ---- Policy clause relevance (deterministic) -------------------------------------------------
# Fired rule id -> policy tags it makes relevant.
RULE_POLICY_TAGS: dict[str, frozenset[str]] = {
    "R_EXACT_HASH": frozenset({"duplicate_financing", "collateral"}),
    "R_FUZZY_MATCH": frozenset({"duplicate_financing", "collateral"}),
    "R_TIMING_OVERLAP": frozenset({"duplicate_financing", "collateral"}),
    "R_SHARED_UBO": frozenset({"related_party"}),
    "R_SHARED_DIRECTOR": frozenset({"related_party"}),
    "R_CORP_OWNERSHIP": frozenset({"related_party"}),
    "R_SAME_ADDRESS": frozenset({"related_party"}),
    "R_SAME_PHONE": frozenset({"related_party"}),
    "R_NO_VESSEL_CALL": frozenset({"collateral"}),
    "R_DOC_MISMATCH": frozenset({"collateral"}),
}
# Tags that are relevant to every report.
ALWAYS_TAGS = frozenset({"str_filing", "hold"})
# Keyword fallback (clause title/text, lower-case) used only when a clause carries no tags.
KEYWORD_TAGS: dict[str, tuple[str, ...]] = {
    "duplicate_financing": ("duplicate", "pledge", "re-issu", "reissu"),
    "collateral": ("collateral", "bill of lading", "warehouse receipt"),
    "related_party": ("related", "beneficial owner", "ownership", "round-tripping"),
    "str_filing": ("suspicious transaction report", " str ", "fiu-ind"),
    "hold": ("hold", "disbursement"),
}
_REISSUE_KEYS = ("re-issu", "reissu")
_BL_MATCH_KEYS = {"exact", "bl"}


def _clause_tags(cl: dict) -> set[str]:
    tags = {str(t) for t in (cl.get("tags") or [])}
    if tags:
        return tags
    blob = f" {cl.get('title', '')} {cl.get('text', '')} ".lower()
    return {t for t, kws in KEYWORD_TAGS.items() if any(k in blob for k in kws)}


def _is_reissue_clause(cl: dict) -> bool:
    blob = f"{cl.get('title', '')} {cl.get('text', '')}".lower()
    return any(k in blob for k in _REISSUE_KEYS)


def select_policy_clauses(clauses: list[dict], fired_rules: set[str], match: Any) -> list[dict]:
    """Keep only clauses relevant to the evidence.

    wanted tags = union of RULE_POLICY_TAGS over fired rules. A clause is kept when its tags
    intersect the wanted set, or it carries only ALWAYS_TAGS (str_filing/hold), except:
    - re-issuance clauses require a FUZZY match whose matched_keys lack "exact"/"bl";
    - related_party clauses require a fired ownership/director/UBO/address/phone rule.
    """
    wanted: set[str] = set()
    for r in fired_rules:
        wanted |= RULE_POLICY_TAGS.get(r, frozenset())
    keys = {str(k).lower() for k in match.matched_keys}
    bl_differs = match.match_type.value == "FUZZY" and not (keys & _BL_MATCH_KEYS)
    out = []
    for cl in clauses:
        tags = _clause_tags(cl)
        if _is_reissue_clause(cl) and not bl_differs:
            continue
        if "related_party" in tags and "related_party" not in wanted:
            continue
        if (tags & wanted) or (tags & ALWAYS_TAGS):
            out.append(cl)
    return out


_LINK_PRIORITY = [
    ("R_SHARED_UBO", "a shared ultimate beneficial owner"),
    ("R_CORP_OWNERSHIP", "a corporate ownership link"),
    ("R_SHARED_DIRECTOR", "a shared director"),
    ("R_SAME_ADDRESS", "a shared registered address"),
    ("R_SAME_PHONE", "a shared telephone number"),
]


_MD_SPECIAL = "*_`[]<>|#"
QUOTE_MAX = 80


def _q(value: str | None) -> str:
    """Render an untrusted document value as a quoted, markdown/HTML-escaped, length-capped string."""
    v = " ".join(str(value or "").split())
    if len(v) > QUOTE_MAX:
        v = v[: QUOTE_MAX - 3].rstrip() + "..."
    v = "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in v)
    v = v.replace('"', "'")
    return f'"{v}"'


def _fmt_amount(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return "an unspecified amount"
    return f"{currency or ''} {amount:,.2f}".strip()


def _dedupe(cits: list[Citation]) -> list[Citation]:
    seen: set[tuple] = set()
    out: list[Citation] = []
    for c in cits:
        k = (c.kind, c.ref, c.page)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


class _Section:
    def __init__(self, part: str, title: str) -> None:
        self.part, self.title = part, title
        self.sentences: list[ReportSentence] = []

    def add(self, text: str, cits: list) -> None:
        good = _dedupe([c for c in cits if c is not None])
        if not good or not text.strip():
            log.info("sentence_omitted_no_citation", extra={"ctx": {"part": self.part, "text": text[:80]}})
            return
        self.sentences.append(ReportSentence(text=text, citations=good))

    def build(self) -> ReportSection:
        return ReportSection(part=self.part, title=self.title, sentences=self.sentences)


def _label(store: Any, node: str) -> str:
    """Node id -> display label. Names only if a store resolves them (registry is public data)."""
    if store is None or ":" not in node:
        return node
    kind, _, rid = node.partition(":")
    try:
        if kind == "company":
            c = store.get_company(rid)
            if c:
                return f"{c['name']} ({rid})"
        elif kind == "person":
            p = store.get_person(rid)
            if p:
                return f"{p['name']} ({rid})"
    except Exception:  # pragma: no cover - defensive
        pass
    return node


def draft_str(
    req: FinancingRequest,
    fields: ExtractedFields,
    inv: Investigation,
    conf: ConfidenceResult,
    settings: Settings,
    *,
    store: Any = None,
    narrative_fn: Callable[[str], str] | None = None,
    now: datetime | None = None,
) -> STRDraft:
    src = fields.sources
    req_cit = Citation(CitationKind.REGISTRY, f"requests:{req.request_id}",
                       snippet=f"financing request {req.request_id}")
    match_cit = inv.match.citation

    # ---- PART 1
    p1 = _Section("PART 1", "Reporting entity")
    p1.add(f"Reporting entity: {reporting_entity_name(req.bank_id, settings)} (bank id {req.bank_id}).", [req_cit])
    p1.add(f"The report relates to financing request {req.request_id} submitted on "
           f"{req.submitted_at.date().isoformat()}.", [req_cit])

    # ---- PART 2
    p2 = _Section("PART 2", "Principal officer")
    p2.add(f"Principal officer: {settings.principal_officer}.", [req_cit])

    # ---- PART 3
    p3 = _Section("PART 3", "Transaction details")
    p3.add(f"The borrower (registry id {inv.borrower_id}) requested financing of "
           f"{_fmt_amount(req.amount, req.currency)}.", [req_cit])
    if fields.bl_number:
        p3.add(f"The bill of lading number is {_q(fields.bl_number)}.", [src.get("bl_number")])
    if fields.vessel:
        voy = f", voyage {_q(fields.voyage)}" if fields.voyage else ""
        p3.add(f"The cargo was shipped on vessel {_q(fields.vessel)}{voy}.",
               [src.get("vessel")])
    if fields.port_of_loading and fields.port_of_discharge:
        p3.add(f"The route is {_q(fields.port_of_loading)} to {_q(fields.port_of_discharge)}.",
               [src.get("port_of_loading"), src.get("port_of_discharge")])
    if fields.commodity:
        qty = ""
        if fields.quantity is not None:
            qty = f" ({fields.quantity:,g} {fields.quantity_unit or ''})".replace(" )", ")")
        p3.add(f"The pledged goods are described in the bill of lading as {_q(fields.commodity)}{qty}.", [src.get("commodity")])
    if fields.value is not None:
        p3.add(f"The documented cargo value is {_fmt_amount(fields.value, fields.currency)}.", [src.get("value")])
    if fields.shipment_date:
        p3.add(f"The shipment date is {fields.shipment_date.isoformat()}.", [src.get("shipment_date")])
    for t in inv.transactions[:5]:
        tid = t.get("txn_id")
        if not tid:
            continue
        d = t.get("txn_date")
        d = d.isoformat() if hasattr(d, "isoformat") else str(d)
        p3.add(f"Transaction {tid} on {d}: {_fmt_amount(t.get('amount'), t.get('currency'))} "
               f"({t.get('txn_type', 'n/a')}) at {t.get('bank_id', 'n/a')}.",
               [Citation(CitationKind.TRANSACTION, str(tid))])
    if inv.timing_overlap_days is not None:
        p3.add(f"The same cargo was pledged at another consortium bank within {inv.timing_overlap_days} "
               f"day(s) of this request.", [match_cit])

    # ---- PART 4
    p4 = _Section("PART 4", "Linked individuals and entities")
    edge_cit: dict[str, Citation] = {}
    for path in inv.paths:
        for e in path.edges:
            edge_cit.setdefault(e.dst, e.citation)
            edge_cit.setdefault(e.src, e.citation)
    rule_cits: dict[str, list[Citation]] = {ev.rule_id: ev.citations for ev in conf.evidence}

    def reg(table: str, rid: str) -> Citation:
        return Citation(CitationKind.REGISTRY, f"{table}:{rid}")

    if inv.counterparty_company_id:
        cp = inv.counterparty_company_id
        p4.add(f"The consortium counterparty is resolved to {_label(store, 'company:' + cp)}.",
               [reg("companies", cp)])
    for path in inv.paths:
        for e in path.edges:
            p4.add(f"{_label(store, e.src)} is linked to {_label(store, e.dst)} via {e.relation}.", [e.citation])
    for pid in inv.shared_ubos:
        p4.add(f"Person {_label(store, 'person:' + pid)} is a shared ultimate beneficial owner.",
               [reg("persons", pid)])
    for pid in inv.shared_directors:
        p4.add(f"Person {_label(store, 'person:' + pid)} is a shared director.", [reg("persons", pid)])
    for aid in inv.shared_addresses:
        p4.add(f"Both entities are registered at address {aid}.", [reg("addresses", aid)])
    for ph in inv.shared_phones:
        c = edge_cit.get(f"phone:{ph}") or edge_cit.get(ph)
        if c is None and rule_cits.get("R_SAME_PHONE"):
            c = rule_cits["R_SAME_PHONE"][0]
        p4.add(f"Both entities use the telephone number {ph}.", [c])

    # ---- PART 5
    p5 = _Section("PART 5", "Grounds of suspicion")
    m = inv.match
    keys = ", ".join(m.matched_keys) or "n/a"
    fired = {ev.rule_id: ev for ev in conf.evidence}
    link_txt, link_cits = "no borrower-to-borrower link was established", []
    for rid, desc in _LINK_PRIORITY:
        if rid in fired:
            link_txt, link_cits = f"the strongest link between the borrowers is {desc} (rule {rid})", list(fired[rid].citations)
            break
    p5.add(f"Summary: the cargo of request {req.request_id} matches a pledge lodged at {m.entry.bank_id} "
           f"({m.match_type.value} match); {link_txt}; the confidence score is {conf.score:.2f} "
           f"({conf.band.value}) and the recommended action is to {RECOMMENDED_ACTION.lower().replace('_', ' ')}.",
           [match_cit] + link_cits)
    p5.add(f"The cargo fingerprint ({keys}) matches consortium entry {m.entry.entry_id} lodged by "
           f"{m.entry.bank_id} ({m.match_type.value} match, similarity {m.similarity:.2f}).", [match_cit])
    for ev in conf.evidence:
        p5.add(f"Rule {ev.rule_id} (weight {ev.weight:.2f}): {ev.description}", list(ev.citations))
    # physical-cargo / cross-document findings: dedicated sentences citing the documents and registry rows checked
    if "R_NO_VESSEL_CALL" in fired:
        e = fired["R_NO_VESSEL_CALL"]
        p5.add(f"Physical-cargo check: the vessel and voyage on the bill of lading have no recorded port call at "
               f"the port of loading around the shipment date in the port-call data, so the pledged cargo may not "
               f"exist.", [c for c in e.citations if c.kind != CitationKind.RULE] or list(e.citations))
    if "R_DOC_MISMATCH" in fired:
        e = fired["R_DOC_MISMATCH"]
        doc_cits = [c for c in e.citations if c.kind == CitationKind.DOCUMENT]
        p5.add("Cross-document check: the bill of lading, invoice, letter of credit and warehouse receipt do not "
               "agree with each other (see the quoted document lines cited).", doc_cits or list(e.citations))
    all_ev_cits = _dedupe([c for ev in conf.evidence for c in ev.citations] + [match_cit])
    p5.add(f"The deterministic confidence score is {conf.score:.2f} ({conf.band.value}).", all_ev_cits)
    for cl in select_policy_clauses(inv.policy_clauses, set(fired), m):
        cid = cl.get("clause_id")
        if not cid:
            continue
        p5.add(f"Policy clause {cid} ({cl.get('title', '')}) is engaged: {cl.get('text', '')}".rstrip(),
               [Citation(CitationKind.POLICY, str(cid), snippet=cl.get("title"))])
    for d in req.documents:
        if detect_injection(d.text):
            p5.add(f"Document {d.doc_id} contains text resembling instructions to the reviewing system; "
                   f"it was treated as data and ignored.",
                   [Citation(CitationKind.DOCUMENT, d.doc_id, snippet="instruction-like text detected")])
    if narrative_fn is not None:
        facts = " ".join(s.text for s in p5.sentences)
        try:
            text = (narrative_fn(facts) or "").strip()
        except Exception as exc:  # LLM failure must not break the report
            log.warning("narrative_fn_failed", extra={"ctx": {"error": str(exc)}})
            text = ""
        if text:
            used = _dedupe([c for s in p5.sentences for c in s.citations])
            p5.sentences.insert(min(1, len(p5.sentences)), ReportSentence(text=text, citations=used))

    # ---- PART 6
    p6 = _Section("PART 6", "Action taken / recommended")
    p6.add(f"Recommended action: hold disbursement of {_fmt_amount(req.amount, req.currency)} on request "
           f"{req.request_id} pending review.", all_ev_cits)
    p6.add("The case is submitted for approval by a named compliance officer before any filing or hold.",
           [match_cit])

    sections = [s.build() for s in (p1, p2, p3, p4, p5, p6)]
    draft = STRDraft(
        report_id="STR-" + req.request_id,
        request_id=req.request_id,
        reporting_bank_id=req.bank_id,
        created_at=now or datetime.now(timezone.utc),
        sections=sections,
        recommended_action=RECOMMENDED_ACTION,
    )
    log.info("str_drafted", extra={"ctx": {"report_id": draft.report_id,
                                           "sentences": sum(len(s.sentences) for s in sections)}})
    return draft


def validate_citations(draft: STRDraft) -> list[str]:
    errors: list[str] = []
    if not draft.sections:
        errors.append("report has no sections")
    if not draft.recommended_action:
        errors.append("recommended_action is empty")
    for sec in draft.sections:
        if not sec.sentences:
            errors.append(f"{sec.part}: section has no sentences")
        for i, s in enumerate(sec.sentences, 1):
            where = f"{sec.part} sentence {i}"
            if not s.text or not s.text.strip():
                errors.append(f"{where}: empty text")
            if not s.citations:
                errors.append(f"{where}: no citations: {s.text[:60]!r}")
                continue
            for c in s.citations:
                if not isinstance(c, Citation) or not isinstance(c.kind, CitationKind):
                    errors.append(f"{where}: invalid citation kind")
                elif not c.ref or not str(c.ref).strip():
                    errors.append(f"{where}: citation with empty ref")
    return errors


def render_markdown(draft: STRDraft) -> str:
    index: dict[tuple, int] = {}
    notes: list[Citation] = []
    lines = [f"# Suspicious Transaction Report {draft.report_id}", "",
             f"- Request: {draft.request_id}",
             f"- Reporting bank: {draft.reporting_bank_id}",
             f"- Created: {draft.created_at.isoformat()}",
             f"- Recommended action: {draft.recommended_action}", ""]
    for sec in draft.sections:
        lines += [f"## {sec.part}: {sec.title}", ""]
        for s in sec.sentences:
            marks = ""
            for c in s.citations:
                k = (c.kind, c.ref, c.page)
                if k not in index:
                    index[k] = len(notes) + 1
                    notes.append(c)
                marks += f"[^{index[k]}]"
            lines.append(f"{s.text}{marks}")
            lines.append("")
    lines += ["## Citations", ""]
    for n, c in enumerate(notes, 1):
        parts = [f"{c.kind.value}: {c.ref}"]
        if c.page is not None:
            parts.append(f"page {c.page}")
        if c.snippet:
            parts.append(f'"{c.snippet}"')
        lines.append(f"[^{n}]: " + ", ".join(parts))
    return "\n".join(lines) + "\n"
