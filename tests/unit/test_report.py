from datetime import date, datetime, timezone

from suraksha.agents.report import draft_str, render_markdown, validate_citations
from suraksha.config import Settings
from suraksha.models import (
    Citation, CitationKind as CK, ConfidenceBand, ConfidenceResult, ConsortiumEntry,
    ConsortiumMatch, Evidence, ExtractedFields, FinancingRequest, GraphEdge, Investigation,
    MatchType, OwnershipPath, ReportSentence,
)


def build_inputs():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    req = FinancingRequest("REQ-1", "BANK_A", "C1", 1_250_000.0, "USD", now)
    fields = ExtractedFields(
        "REQ-1", bl_number="BL123", vessel="MV Star", voyage="V9", port_of_loading="Mundra",
        port_of_discharge="Dubai", commodity="Copper", quantity=500, quantity_unit="MT",
        value=1_250_000.0, currency="USD", shipment_date=date(2026, 2, 20),
        sources={k: Citation(CK.DOCUMENT, "DOC-1", page=1, snippet=k) for k in
                 ["bl_number", "vessel", "voyage", "port_of_loading", "port_of_discharge",
                  "commodity", "quantity", "value", "shipment_date"]},
    )
    mc = Citation(CK.CONSORTIUM, "E9")
    entry = ConsortiumEntry("E9", "BANK_B", {"cargo": "h"}, 10, "tok", now)
    match = ConsortiumMatch("REQ-1", entry, MatchType.EXACT, ["exact", "cargo"], 1.0, mc)
    edge = GraphEdge("company:C1", "person:P1", "UBO_OF", Citation(CK.REGISTRY, "roles:C1:P1"))
    edge2 = GraphEdge("person:P1", "company:C2", "UBO_OF", Citation(CK.REGISTRY, "roles:C2:P1"))
    inv = Investigation(
        "REQ-1", "C1", "C2", match, [OwnershipPath("C1", "C2", [edge, edge2])],
        ["P2"], ["P1"], ["A1"], ["+911234"],
        [{"txn_id": "T1", "company_id": "C1", "bank_id": "BANK_A", "txn_date": date(2026, 1, 5),
          "amount": 5000.0, "currency": "USD", "counterparty": "X", "txn_type": "LC"}],
        [{"clause_id": "TF-4.2", "section": "4", "title": "Duplicate financing", "text": "Escalate.", "tags": []}],
        12,
    )
    conf = ConfidenceResult("REQ-1", 0.8, ConfidenceBand.HIGH, [
        Evidence("R_EXACT_HASH", "Identical cargo fingerprint.", 0.5, [mc]),
        Evidence("R_SHARED_UBO", "Shared UBO P1.", 0.3, [Citation(CK.REGISTRY, "roles:C1:P1")]),
    ], [])
    return req, fields, inv, conf


def make():
    req, fields, inv, conf = build_inputs()
    return draft_str(req, fields, inv, conf, Settings())


def test_structure_and_citations():
    d = make()
    assert d.report_id == "STR-REQ-1"
    assert d.recommended_action == "HOLD_DISBURSEMENT"
    assert [s.part for s in d.sections] == [f"PART {i}" for i in range(1, 7)]
    for sec in d.sections:
        assert sec.sentences
        for s in sec.sentences:
            assert s.citations
    assert validate_citations(d) == []


def test_amount_formatted_and_no_other_bank_names():
    d = make()
    text = " ".join(s.text for sec in d.sections for s in sec.sentences)
    assert "USD 1,250,000.00" in text
    assert "BANK_B" in text  # bank id only, no customer names


def test_missing_source_omits_sentence():
    req, fields, inv, conf = build_inputs()
    del fields.sources["bl_number"]
    d = draft_str(req, fields, inv, conf, Settings())
    text = " ".join(s.text for sec in d.sections for s in sec.sentences)
    assert "BL123" not in text
    assert validate_citations(d) == []


def test_validate_catches_uncited_sentence():
    d = make()
    d.sections[2].sentences.append(ReportSentence("Injected claim.", []))
    errs = validate_citations(d)
    assert any("no citations" in e for e in errs)


def test_narrative_fn_inherits_citations():
    req, fields, inv, conf = build_inputs()
    d = draft_str(req, fields, inv, conf, Settings(), narrative_fn=lambda facts: "LLM narrative.")
    first = d.sections[4].sentences[0]
    assert first.text == "LLM narrative."
    assert first.citations
    assert validate_citations(d) == []


def test_narrative_fn_failure_is_ignored():
    req, fields, inv, conf = build_inputs()

    def boom(_):
        raise RuntimeError("x")

    d = draft_str(req, fields, inv, conf, Settings(), narrative_fn=boom)
    assert validate_citations(d) == []


def test_markdown_has_footnotes():
    md = render_markdown(make())
    assert "## PART 5" in md
    assert "[^1]" in md and "[^1]:" in md
    assert "document: DOC-1, page 1" in md
    assert "consortium: E9" in md
    assert "policy: TF-4.2" in md
