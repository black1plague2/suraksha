import re

import pytest

from suraksha.agents import report_templates as rt
from suraksha.agents.report import draft_str
from suraksha.config import Settings
from suraksha.models import Citation, CitationKind as CK, Evidence, ReportSentence
from tests.unit.test_report import build_inputs


def make(rules=("R_EXACT_HASH", "R_SHARED_UBO")):
    req, fields, inv, conf = build_inputs()
    conf.evidence = [Evidence(r, "desc", 0.1, [Citation(CK.RULE, r)]) for r in rules]
    return draft_str(req, fields, inv, conf, Settings())


def test_both_jurisdictions_render():
    d = make()
    assert "## PART 5" in rt.render(d, "FIU-IND")
    md = rt.render(d, "UAE-goAML")
    assert "goAML" in md and "## Reason for suspicion" in md and "## Transaction(s)" in md
    assert "## Attachments" in md


def test_every_goaml_sentence_has_footnote():
    md = rt.render(make(), "UAE-goAML")
    body = md.split("## Citations")[0]
    for line in body.splitlines():
        if line and not line.startswith(("#", "- ")):
            assert re.search(r"\[\^\d+\]$", line), line


def test_structured_keys_and_validate():
    d = make()
    for j in rt.JURISDICTIONS:
        st = rt.to_structured(d, j)
        assert {"jurisdiction", "report_id", "sections", "indicators", "recommended_action"} <= st.keys()
        assert rt.validate(st) == []
    ids = [s["id"] for s in rt.to_structured(d, "UAE-goAML")["sections"]]
    assert ids[0] == "reporting_entity" and "indicators" in ids and "action_taken" in ids


def test_unknown_jurisdiction():
    with pytest.raises(ValueError):
        rt.render(make(), "XX")
    with pytest.raises(ValueError):
        rt.to_structured(make(), "XX")


def test_indicators_only_fired_rules():
    st = rt.to_structured(make(("R_EXACT_HASH",)), "UAE-goAML")
    assert st["indicators"] == ["R_EXACT_HASH"]
    sec = next(s for s in st["sections"] if s["id"] == "indicators")
    text = " ".join(s["text"] for s in sec["sentences"])
    assert "R_EXACT_HASH" in text and "R_SHARED_UBO" not in text and "R_DOC_MISMATCH" not in text
    st2 = rt.to_structured(make(("R_DOC_MISMATCH", "R_NO_VESSEL_CALL")), "UAE-goAML")
    assert st2["indicators"] == ["R_DOC_MISMATCH", "R_NO_VESSEL_CALL"]


def test_no_fired_rules_no_indicator_section():
    st = rt.to_structured(make(()), "UAE-goAML")
    assert st["indicators"] == [] and all(s["id"] != "indicators" for s in st["sections"])


def test_validate_catches_uncited():
    d = make()
    d.sections[2].sentences.append(ReportSentence("Injected.", []))
    st = rt.to_structured(d, "UAE-goAML")
    assert any("no citations" in e for e in rt.validate(st))
    with pytest.raises(ValueError):
        rt.render(d, "UAE-goAML")


def test_all_sentences_preserved():
    d = make()
    n = sum(len(s.sentences) for s in d.sections)
    st = rt.to_structured(d, "UAE-goAML")
    mapped = sum(len(s["sentences"]) for s in st["sections"]
                 if s["id"] not in ("indicators", "attachments", "report_details"))
    assert mapped == n
