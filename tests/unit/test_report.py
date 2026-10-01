import dataclasses
from datetime import date, datetime, timezone

from suraksha.agents.report import BANK_NAMES, draft_str, render_markdown, validate_citations
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
    first = d.sections[4].sentences[1]
    assert d.sections[4].sentences[0].text.startswith("Summary:")
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


CLAUSES = [
    {"clause_id": "TF-3.1", "title": "Single-pledge principle", "text": "Duplicate financing.", "tags": ["duplicate_financing", "collateral"]},
    {"clause_id": "TF-4.2", "title": "Document re-issuance", "text": "Suspected re-issued document.", "tags": ["duplicate_financing", "collateral", "hold"]},
    {"clause_id": "TF-5.3", "title": "Related-party borrowers", "text": "Related parties escalate.", "tags": ["related_party", "duplicate_financing"]},
    {"clause_id": "AML-7.1", "title": "Round-tripping", "text": "Layering.", "tags": ["related_party", "str_filing"]},
    {"clause_id": "AML-7.4", "title": "STR filing obligation", "text": "File STR.", "tags": ["str_filing"]},
    {"clause_id": "TF-6.1", "title": "Disbursement hold", "text": "Hold.", "tags": ["hold", "duplicate_financing"]},
]


def clause_ids(d):
    return {s.citations[0].ref for s in d.sections[4].sentences if s.citations[0].kind == CK.POLICY}


def run(rules, match_type=MatchType.EXACT, keys=("exact", "cargo")):
    req, fields, inv, conf = build_inputs()
    inv.policy_clauses = CLAUSES
    inv.match.match_type = match_type
    inv.match.matched_keys = list(keys)
    conf.evidence = [Evidence(r, "d", 0.1, [Citation(CK.RULE, r)]) for r in rules]
    return draft_str(req, fields, inv, conf, Settings())


def test_clauses_exact_with_ubo():
    ids = clause_ids(run(["R_EXACT_HASH", "R_SHARED_UBO"]))
    assert ids == {"TF-3.1", "TF-5.3", "AML-7.1", "AML-7.4", "TF-6.1"}  # no re-issuance


def test_clauses_no_related_party_without_ownership_rule():
    ids = clause_ids(run(["R_EXACT_HASH"]))
    assert ids == {"TF-3.1", "AML-7.4", "TF-6.1"}


def test_reissue_only_when_fuzzy_and_bl_differs():
    assert "TF-4.2" in clause_ids(run(["R_FUZZY_MATCH"], MatchType.FUZZY, ("vessel", "voyage")))
    assert "TF-4.2" not in clause_ids(run(["R_FUZZY_MATCH"], MatchType.FUZZY, ("bl", "vessel")))
    assert "TF-4.2" not in clause_ids(run(["R_FUZZY_MATCH"], MatchType.EXACT, ("exact",)))


def test_keyword_fallback_for_untagged_clause():
    req, fields, inv, conf = build_inputs()
    inv.policy_clauses = [
        {"clause_id": "X-1", "title": "Duplicate financing", "text": "t", "tags": []},
        {"clause_id": "X-2", "title": "Related-party borrowers", "text": "t", "tags": []},
        {"clause_id": "X-3", "title": "Unrelated topic", "text": "nothing", "tags": []},
    ]
    conf.evidence = [Evidence("R_EXACT_HASH", "d", 0.5, [Citation(CK.RULE, "r")])]
    ids = clause_ids(draft_str(req, fields, inv, conf, Settings()))
    assert ids == {"X-1"}


def test_reporting_entity_follows_bank_id(monkeypatch):
    monkeypatch.delenv("SURAKSHA_REPORTING_ENTITY", raising=False)
    req, fields, inv, conf = build_inputs()
    for bank in ("BANK_B", "BANK_C"):
        d = draft_str(dataclasses.replace(req, bank_id=bank), fields, inv, conf, Settings())
        assert BANK_NAMES[bank] in d.sections[0].sentences[0].text
    d = draft_str(dataclasses.replace(req, bank_id="BANK_Z"), fields, inv, conf, Settings())
    assert "Reporting entity: BANK_Z" in d.sections[0].sentences[0].text


def test_reporting_entity_env_override(monkeypatch):
    monkeypatch.setenv("SURAKSHA_REPORTING_ENTITY", "Custom Bank")
    req, fields, inv, conf = build_inputs()
    d = draft_str(req, fields, inv, conf, Settings(reporting_entity="Custom Bank"))
    assert "Custom Bank" in d.sections[0].sentences[0].text


def test_executive_summary_first_and_cited():
    d = make()
    s = d.sections[4].sentences[0]
    assert s.text.startswith("Summary:")
    assert "BANK_B" in s.text and "EXACT" in s.text and "shared ultimate beneficial owner" in s.text
    assert "0.80" in s.text and "HIGH" in s.text and "hold disbursement" in s.text
    assert s.citations and any(c.kind == CK.CONSORTIUM for c in s.citations)
    assert validate_citations(d) == []
