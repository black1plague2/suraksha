"""Jurisdiction templates for the STR draft.

The cited sentences produced by ``report.draft_str`` are the single source of truth. A
jurisdiction template only RE-MAPS those same sentences into a different section layout; it never
creates a claim without citations (G3). Indicator sentences are derived from rule sentences that
actually fired and inherit those sentences' citations plus a RULE citation.
"""
from __future__ import annotations

import re
from typing import Any

from suraksha.agents.report import render_markdown
from suraksha.models import Citation, CitationKind, ReportSentence, STRDraft

JURISDICTIONS = ("FIU-IND", "UAE-goAML")

# Suraksha rule id -> (indicator name, description). Descriptive only; NOT official goAML codes.
RED_FLAGS: dict[str, tuple[str, str]] = {
    "R_EXACT_HASH": ("Multiple financing against the same documents",
                     "The same shipping documents/cargo fingerprint were presented to more than one bank."),
    "R_FUZZY_MATCH": ("Near-duplicate / re-issued trade documents",
                      "Cargo details closely match another bank's pledge while document identifiers differ."),
    "R_SHARED_UBO": ("Related-party counterparties (common beneficial owner)",
                     "Borrower and counterparty share an ultimate beneficial owner."),
    "R_SHARED_DIRECTOR": ("Related-party counterparties (common director)",
                          "Borrower and counterparty share a director."),
    "R_CORP_OWNERSHIP": ("Related-party counterparties (corporate ownership link)",
                         "Borrower and counterparty are linked through corporate ownership."),
    "R_SAME_ADDRESS": ("Related-party counterparties (shared address)",
                       "Borrower and counterparty share a registered address."),
    "R_SAME_PHONE": ("Related-party counterparties (shared telephone)",
                     "Borrower and counterparty share a telephone number."),
    "R_TIMING_OVERLAP": ("Same cargo financed in overlapping periods",
                         "The same cargo was pledged at different banks within a short window."),
    "R_NO_VESSEL_CALL": ("Shipment inconsistent with vessel movements",
                         "The declared vessel has no recorded call at the declared port in the shipment window."),
    "R_DOC_MISMATCH": ("Document inconsistencies",
                       "Values or details differ across the trade documents presented."),
}

# FIU-IND part -> goAML section (deterministic).
PART_TO_SECTION: dict[str, tuple[str, str]] = {
    "PART 1": ("reporting_entity", "Reporting entity"),
    "PART 2": ("reporting_person", "Reporting person / compliance officer"),
    "PART 3": ("transactions", "Transaction(s)"),
    "PART 4": ("involved_parties", "Involved parties (persons / entities)"),
    "PART 5": ("reason", "Reason for suspicion"),
    "PART 6": ("action_taken", "Action taken"),
}
SECTION_ORDER = ["reporting_entity", "reporting_person", "report_details", "transactions",
                 "involved_parties", "reason", "indicators", "action_taken", "attachments"]
SECTION_TITLES = {sid: title for sid, title in PART_TO_SECTION.values()}
SECTION_TITLES.update({
    "report_details": "Report details",
    "indicators": "Reasons / indicators (fired rules only)",
    "attachments": "Attachments / supporting documents",
})
_RULE_RE = re.compile(r"^Rule (R_[A-Z0-9_]+)\b")


def _cit_dict(c: Citation) -> dict:
    return {"kind": c.kind.value, "ref": c.ref, "page": c.page, "snippet": c.snippet}


def _sent(s: ReportSentence) -> dict:
    return {"text": s.text, "citations": [_cit_dict(c) for c in s.citations]}


def fired_rules(draft: STRDraft) -> list[str]:
    """Rule ids that fired, taken from the 'Rule R_X (weight ...)' evidence sentences, in order."""
    out: list[str] = []
    for sec in draft.sections:
        for s in sec.sentences:
            m = _RULE_RE.match(s.text)
            if m and m.group(1) not in out:
                out.append(m.group(1))
    return out


def _indicator_sentences(draft: STRDraft) -> list[dict]:
    res: list[dict] = []
    for sec in draft.sections:
        for s in sec.sentences:
            m = _RULE_RE.match(s.text)
            if not m or m.group(1) not in RED_FLAGS or not s.citations:
                continue
            rid = m.group(1)
            if any(r["rule_id"] == rid for r in res):
                continue
            name, desc = RED_FLAGS[rid]
            seen: set = set()
            uniq: list[Citation] = []
            for c in [Citation(CitationKind.RULE, rid)] + list(s.citations):
                k = (c.kind, c.ref, c.page)
                if k not in seen:
                    seen.add(k)
                    uniq.append(c)
            res.append({"rule_id": rid, "text": f"Indicator {rid} ({name}): {desc}",
                        "citations": [_cit_dict(c) for c in uniq]})
    return res


def _goaml(draft: STRDraft) -> dict:
    sections: dict[str, list[dict]] = {k: [] for k in SECTION_ORDER}
    for sec in draft.sections:
        sid, _ = PART_TO_SECTION.get(sec.part, ("reason", ""))
        sections[sid] += [_sent(s) for s in sec.sentences]
    inds = _indicator_sentences(draft)
    sections["indicators"] = [{"text": i["text"], "citations": i["citations"]} for i in inds]
    docs: dict[str, Citation] = {}
    for sec in draft.sections:
        for s in sec.sentences:
            for c in s.citations:
                if c.kind == CitationKind.DOCUMENT:
                    docs.setdefault(c.ref, c)
    sections["attachments"] = [
        {"text": f"Supporting document {ref} is relied on in this report.", "citations": [_cit_dict(c)]}
        for ref, c in docs.items()]
    # Report type is metadata; the one claim (recommended action) cites the action section.
    action_cits = [c for s in sections["action_taken"][:1] for c in s["citations"]]
    if action_cits:
        sections["report_details"] = [{
            "text": f"Report type STR (transactions included); recommended action {draft.recommended_action}.",
            "citations": action_cits}]
    return {
        "jurisdiction": "UAE-goAML",
        "report_id": draft.report_id,
        "request_id": draft.request_id,
        "reporting_bank_id": draft.reporting_bank_id,
        "created_at": draft.created_at.isoformat(),
        "report_type": "STR",
        "recommended_action": draft.recommended_action,
        "indicators": [i["rule_id"] for i in inds],
        "sections": [{"id": k, "title": SECTION_TITLES[k], "sentences": sections[k]}
                     for k in SECTION_ORDER if sections[k]],
    }


def _fiu_ind(draft: STRDraft) -> dict:
    return {
        "jurisdiction": "FIU-IND",
        "report_id": draft.report_id,
        "request_id": draft.request_id,
        "reporting_bank_id": draft.reporting_bank_id,
        "created_at": draft.created_at.isoformat(),
        "report_type": "STR",
        "recommended_action": draft.recommended_action,
        "indicators": fired_rules(draft),
        "sections": [{"id": s.part, "title": s.title, "sentences": [_sent(x) for x in s.sentences]}
                     for s in draft.sections],
    }


def _check(jurisdiction: str) -> None:
    if jurisdiction not in JURISDICTIONS:
        raise ValueError(f"unknown jurisdiction {jurisdiction!r}; expected one of {JURISDICTIONS}")


def to_structured(draft: STRDraft, jurisdiction: str) -> dict[str, Any]:
    _check(jurisdiction)
    return _fiu_ind(draft) if jurisdiction == "FIU-IND" else _goaml(draft)


def validate(structured: dict) -> list[str]:
    """G3 check on a structured report: every sentence has >=1 well-formed citation."""
    errors: list[str] = []
    secs = structured.get("sections") or []
    if not secs:
        errors.append("report has no sections")
    for sec in secs:
        if not sec.get("sentences"):
            errors.append(f"{sec.get('id')}: section has no sentences")
        for i, s in enumerate(sec.get("sentences", []), 1):
            where = f"{sec.get('id')} sentence {i}"
            if not (s.get("text") or "").strip():
                errors.append(f"{where}: empty text")
            cits = s.get("citations") or []
            if not cits:
                errors.append(f"{where}: no citations: {(s.get('text') or '')[:60]!r}")
            for c in cits:
                if not c.get("kind") or not str(c.get("ref") or "").strip():
                    errors.append(f"{where}: malformed citation")
    return errors


def render(draft: STRDraft, jurisdiction: str) -> str:
    _check(jurisdiction)
    if jurisdiction == "FIU-IND":
        return render_markdown(draft)
    st = _goaml(draft)
    errs = validate(st)
    if errs:
        raise ValueError("uncited content in goAML structure: " + "; ".join(errs))
    index: dict[tuple, int] = {}
    notes: list[dict] = []
    lines = [f"# UAE FIU goAML Suspicious Transaction Report {draft.report_id}", "",
             "- Report type: STR (transactions included)",
             f"- Request: {draft.request_id}",
             f"- Reporting bank: {draft.reporting_bank_id}",
             f"- Created: {st['created_at']}",
             f"- Recommended action: {draft.recommended_action}",
             "- Structure mapped from the FIU-IND draft; field-level layout to be verified with UAE FIU goAML guidance.",
             ""]
    for sec in st["sections"]:
        lines += [f"## {sec['title']}", ""]
        for s in sec["sentences"]:
            marks = ""
            for c in s["citations"]:
                k = (c["kind"], c["ref"], c["page"])
                if k not in index:
                    index[k] = len(notes) + 1
                    notes.append(c)
                marks += f"[^{index[k]}]"
            lines += [f"{s['text']}{marks}", ""]
    lines += ["## Citations", ""]
    for n, c in enumerate(notes, 1):
        parts = [f"{c['kind']}: {c['ref']}"]
        if c["page"] is not None:
            parts.append(f"page {c['page']}")
        if c["snippet"]:
            parts.append('"' + c["snippet"] + '"')
        lines.append(f"[^{n}]: " + ", ".join(parts))
    return "\n".join(lines) + "\n"
