"""Report agent: drafts an STR in FIU-IND structure with a citation on every sentence.

Deterministic, template based. An optional ``narrative_fn`` (e.g. Cortex complete) may add a
narrative sentence to PART 5; that sentence inherits the citations of the evidence it was
generated from, so the "no uncited sentence" invariant still holds.
Rule: if a needed citation is missing, the sentence is omitted rather than emitted uncited.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from suraksha.config import Settings
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
    p1.add(f"Reporting entity: {settings.reporting_entity} (bank id {req.bank_id}).", [req_cit])
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
        p3.add(f"The bill of lading number is {fields.bl_number}.", [src.get("bl_number")])
    if fields.vessel:
        voy = f", voyage {fields.voyage}" if fields.voyage else ""
        p3.add(f"The cargo was shipped on vessel {fields.vessel}{voy}.",
               [src.get("vessel")])
    if fields.port_of_loading and fields.port_of_discharge:
        p3.add(f"The route is {fields.port_of_loading} to {fields.port_of_discharge}.",
               [src.get("port_of_loading"), src.get("port_of_discharge")])
    if fields.commodity:
        qty = ""
        if fields.quantity is not None:
            qty = f" ({fields.quantity:,g} {fields.quantity_unit or ''})".replace(" )", ")")
        p3.add(f"The pledged goods are {fields.commodity}{qty}.", [src.get("commodity")])
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
    p5.add(f"The cargo fingerprint ({keys}) matches consortium entry {m.entry.entry_id} lodged by "
           f"{m.entry.bank_id} ({m.match_type.value} match, similarity {m.similarity:.2f}).", [match_cit])
    for ev in conf.evidence:
        p5.add(f"Rule {ev.rule_id} (weight {ev.weight:.2f}): {ev.description}", list(ev.citations))
    all_ev_cits = _dedupe([c for ev in conf.evidence for c in ev.citations] + [match_cit])
    p5.add(f"The deterministic confidence score is {conf.score:.2f} ({conf.band.value}).", all_ev_cits)
    for cl in inv.policy_clauses:
        cid = cl.get("clause_id")
        if not cid:
            continue
        p5.add(f"Policy clause {cid} ({cl.get('title', '')}) is engaged: {cl.get('text', '')}".rstrip(),
               [Citation(CitationKind.POLICY, str(cid), snippet=cl.get("title"))])
    if narrative_fn is not None:
        facts = " ".join(s.text for s in p5.sentences)
        try:
            text = (narrative_fn(facts) or "").strip()
        except Exception as exc:  # LLM failure must not break the report
            log.warning("narrative_fn_failed", extra={"ctx": {"error": str(exc)}})
            text = ""
        if text:
            used = _dedupe([c for s in p5.sentences for c in s.citations])
            p5.sentences.insert(0, ReportSentence(text=text, citations=used))

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
