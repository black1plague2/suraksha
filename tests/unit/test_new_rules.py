"""ITER-06: physical-cargo (R_NO_VESSEL_CALL) and cross-document (R_DOC_MISMATCH) rules."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

import pytest

from suraksha.agents import confidence, intake, investigator
from suraksha.agents.report import validate_citations
from suraksha.config import RULE_WEIGHTS, get_settings
from suraksha.models import CitationKind, ConfidenceBand, Document, PipelineStatus
from suraksha.pipeline import Suraksha
from suraksha.store.batch import facts_row
from suraksha.store.memory import MemoryStore
from suraksha.synth import generate, load_into

S = get_settings()


@pytest.fixture(scope="module")
def ds():
    return generate(42)


@pytest.fixture()
def store(ds):
    st = MemoryStore()
    load_into(st, ds)
    return st


def of(ds, scn):
    return [r for r in ds.requests if ds.scenarios[r.request_id] == scn]


def checks(req, store):
    f = intake.extract(req)
    return f, investigator.document_checks(req, f, store, S)


def with_text(req, doc_type, fn):
    docs = [replace(d, text=fn(d.text)) if d.doc_type.value == doc_type else d for d in req.documents]
    return replace(req, documents=docs)


# ------------------------------------------------------------------ synthetic data
def test_synth_has_vessel_calls_and_new_scenarios(ds):
    assert ds.vessel_calls and {"vessel", "voyage", "port", "arrived", "departed"} == set(ds.vessel_calls[0])
    for scn, n in (("phantom_no_vessel_call", 5), ("doc_mismatch_qty", 4), ("doc_mismatch_value", 4),
                   ("decoy_minor_rounding", 3), ("decoy_vessel_call_edge", 3)):
        assert len(of(ds, scn)) == n, scn
    assert generate(42).vessel_calls == ds.vessel_calls  # deterministic


def test_existing_requests_unchanged_by_new_scenarios(ds):
    base = generate(42, n_phantom=0, n_doc_qty=0, n_doc_value=0, n_decoy_rounding=0, n_decoy_edge=0)
    new_ids = {r.request_id for r in ds.requests if ds.scenarios[r.request_id] in (
        "phantom_no_vessel_call", "doc_mismatch_qty", "doc_mismatch_value", "decoy_minor_rounding",
        "decoy_vessel_call_edge")}
    old = [r for r in ds.requests if r.request_id not in new_ids]
    assert [(r.request_id, r.documents[0].text) for r in old] == [(r.request_id, r.documents[0].text)
                                                                   for r in base.requests]


# ------------------------------------------------------------------ R_NO_VESSEL_CALL
def test_legit_shipments_have_a_vessel_call(ds, store):
    for r in of(ds, "clean")[:15]:
        f, ev = checks(r, store)
        assert not [e for e in ev if e.rule_id == "R_NO_VESSEL_CALL"], r.request_id


def test_phantom_fires_with_citations(ds, store):
    for r in of(ds, "phantom_no_vessel_call"):
        f, ev = checks(r, store)
        e = next(e for e in ev if e.rule_id == "R_NO_VESSEL_CALL")
        assert e.weight == RULE_WEIGHTS["R_NO_VESSEL_CALL"] == 0.35
        kinds = {c.kind for c in e.citations}
        assert CitationKind.REGISTRY in kinds and CitationKind.DOCUMENT in kinds
        assert any(c.ref.startswith("vessel_calls:") for c in e.citations)
        assert "cargo may not exist" in e.description
        assert "query scope" in e.description or "other ports" in e.description


def test_edge_call_8_days_off_does_not_fire(ds, store):
    for r in of(ds, "decoy_vessel_call_edge"):
        _, ev = checks(r, store)
        assert not ev, r.request_id


def test_window_boundary(store):
    req = of(generate(42), "clean")[0]
    f = intake.extract(req)
    # shift the shipment date so the nearest call is exactly 10 vs 11 days away
    call = next(c for c in store.vessel_calls(f.vessel, f.voyage) if c["port"].upper() == f.port_of_loading.upper())
    ok = replace(f, shipment_date=date.fromordinal(call["departed"].toordinal() + 10))
    bad = replace(f, shipment_date=date.fromordinal(call["departed"].toordinal() + 11))
    assert investigator.vessel_call_check(ok, store, S) is None
    assert investigator.vessel_call_check(bad, store, S) is not None


def test_missing_fields_no_fire_but_ask(ds, store):
    req = of(ds, "phantom_no_vessel_call")[0]
    f = intake.extract(req)
    for missing in ("vessel", "voyage", "port_of_loading", "shipment_date"):
        g = replace(f, **{missing: None})
        assert investigator.vessel_call_check(g, store, S) is None
        gaps = investigator.document_gaps(req, g, store)
        assert any("Physical-cargo check not run" in x for x in gaps)


def test_no_feed_no_fire_but_ask(ds):
    empty = MemoryStore()
    empty.load_registry(ds.companies, ds.persons, ds.roles, ds.corp_owners, ds.addresses, [], [])  # old call style
    req = of(ds, "phantom_no_vessel_call")[0]
    f = intake.extract(req)
    assert investigator.vessel_call_check(f, empty, S) is None
    assert any("no port-call" in g for g in investigator.document_gaps(req, f, empty))


def test_vessel_name_normalisation(ds, store):
    f = intake.extract(of(ds, "clean")[0])
    for v in ("M/V " + f.vessel.upper(), "MV  " + f.vessel.lower()):
        assert store.vessel_calls(v, f.voyage) == store.vessel_calls(f.vessel, f.voyage) != []


# ------------------------------------------------------------------ R_DOC_MISMATCH
def test_mismatch_scenarios_fire_with_both_lines_cited(ds, store):
    for scn in ("doc_mismatch_qty", "doc_mismatch_value"):
        for r in of(ds, scn):
            _, ev = checks(r, store)
            e = next(e for e in ev if e.rule_id == "R_DOC_MISMATCH")
            docs = {c.ref for c in e.citations if c.kind == CitationKind.DOCUMENT}
            assert len(docs) >= 2 and all(c.snippet for c in e.citations if c.kind == CitationKind.DOCUMENT)
            assert e.weight == 0.25


def test_minor_rounding_decoy_clean(ds, store):
    for r in of(ds, "decoy_minor_rounding"):
        _, ev = checks(r, store)
        assert not ev


def test_commodity_mismatch(ds, store):
    req = of(ds, "clean")[0]
    new = with_text(req, "INVOICE", lambda t: re.sub(r"^Goods: .*$", "Goods: Granular Urea", t, flags=re.M))
    new = with_text(new, "LC", lambda t: re.sub(r"^Goods: .*$", "Goods: Granular Urea", t, flags=re.M))
    if "UREA" in intake.extract(req).commodity.upper():
        pytest.skip("random clean cargo is urea")
    ev = investigator.doc_mismatch_check(new, S)
    assert ev is not None and "commodity differs" in ev.description


def test_unit_normalisation_not_a_mismatch(ds, store):
    req = of(ds, "clean")[0]
    f = intake.extract(req)
    kg = f"Quantity: {f.quantity * 1000:.0f} KG"
    new = with_text(req, "INVOICE", lambda t: re.sub(r"^Quantity: .*$", kg, t, flags=re.M))
    assert investigator.doc_mismatch_check(new, S) is None


def test_wr_exceeding_bl_is_called_out(ds, store):
    req = of(ds, "doc_mismatch_qty")
    wr = next((r for r in req if "exceeds the B/L" in (investigator.doc_mismatch_check(r, S).description)), None)
    assert wr is not None


def test_single_document_asks_instead(ds, store):
    req = replace(of(ds, "clean")[0], documents=[d for d in of(ds, "clean")[0].documents if d.doc_type.value == "BL"])
    f = intake.extract(req)
    assert investigator.doc_mismatch_check(req, S) is None
    assert any("Cross-document check not run" in g for g in investigator.document_gaps(req, f, store))


# ------------------------------------------------------------------ scoring
def test_score_standalone_never_high(ds, store):
    r = of(ds, "phantom_no_vessel_call")[0]
    _, ev = checks(r, store)
    both = ev + checks(of(ds, "doc_mismatch_qty")[0], store)[1]
    conf = confidence.score_standalone(both, S, request_id="X")
    assert conf.score == 0.60 and conf.band == ConfidenceBand.LOW  # = threshold, still never HIGH
    assert conf.missing and conf.request_id == "X"


def test_weights_in_config_and_sql():
    assert RULE_WEIGHTS["R_NO_VESSEL_CALL"] == 0.35 and RULE_WEIGHTS["R_DOC_MISMATCH"] == 0.25
    sql = open("sql/04_rules.sql", encoding="utf-8").read()
    for rid, w in (("R_NO_VESSEL_CALL", "0.35"), ("R_DOC_MISMATCH", "0.25")):
        assert re.search(rf"\('{rid}',\s+{w},", sql)
    for col in ("no_vessel_call", "doc_mismatch"):
        assert col in sql
    assert "f.match_type IS NOT NULL" in sql


# ------------------------------------------------------------------ pipeline (needs the pipeline.py hook)
def test_pipeline_standalone_outcomes(ds, store):
    app = Suraksha(store)
    res = {r.request_id: app.process(r) for r in ds.requests}
    for scn, want in (("phantom_no_vessel_call", PipelineStatus.NEED_MORE_EVIDENCE),
                      ("doc_mismatch_qty", PipelineStatus.NEED_MORE_EVIDENCE),
                      ("doc_mismatch_value", PipelineStatus.NEED_MORE_EVIDENCE),
                      ("decoy_minor_rounding", PipelineStatus.CLEAR),
                      ("decoy_vessel_call_edge", PipelineStatus.CLEAR)):
        for r in of(ds, scn):
            out = res[r.request_id]
            assert out.status == want, (scn, out.status)
            if want != PipelineStatus.CLEAR:
                assert out.report is None and out.case is None  # never an auto-STR without a consortium match
                assert out.investigation is None and out.confidence.band == ConfidenceBand.LOW
                row = facts_row(out)
                assert row[1] is None and (row[9] or row[10])
    assert app.audit.verify()


def test_doc_rule_folds_into_matched_case_and_report(ds, store):
    """A consortium duplicate whose invoice quantity is also inflated: score includes R_DOC_MISMATCH and PART 5
    cites both document lines."""
    app = Suraksha(store)
    dup = next(r for r in ds.requests if ds.scenarios[r.request_id] == "dup_exact_shell")
    for r in ds.requests:
        if r.request_id != dup.request_id:
            app.process(r)
    base = app.process(dup)
    assert base.status == PipelineStatus.PENDING_APPROVAL
    assert "R_DOC_MISMATCH" not in {e.rule_id for e in base.confidence.evidence}

    st2 = MemoryStore()
    load_into(st2, ds)
    app2 = Suraksha(st2)
    for r in ds.requests:
        if r.request_id != dup.request_id:
            app2.process(r)
    f = intake.extract(dup)
    bad = with_text(dup, "INVOICE", lambda t: re.sub(r"^Quantity: .*$", f"Quantity: {f.quantity * 1.3:.1f} MT", t,
                                                      flags=re.M))
    res = app2.process(bad)
    ids = {e.rule_id for e in res.confidence.evidence}
    assert "R_DOC_MISMATCH" in ids and res.confidence.score >= base.confidence.score
    assert res.status == PipelineStatus.PENDING_APPROVAL
    assert not validate_citations(res.report)
    part5 = next(s for s in res.report.sections if s.part == "PART 5")
    cross = [s for s in part5.sentences if s.text.startswith("Cross-document check")]
    assert cross and len({c.ref for c in cross[0].citations if c.kind == CitationKind.DOCUMENT}) >= 2


def test_report_physical_cargo_sentence(ds, store):
    """Matched duplicate whose cargo has no port call: PART 5 carries the cited physical-cargo sentence."""
    dup = next(r for r in ds.requests if ds.scenarios[r.request_id] == "dup_exact_shell")
    st = MemoryStore()
    load_into(st, ds)
    text = re.sub(r"\s+", " ", dup.documents[3].text)
    gone = [c for c in st.vessel_call_rows if c["vessel"] in text]
    st.vessel_call_rows = [c for c in st.vessel_call_rows if c["vessel"] not in text]
    # keep the vessel KNOWN to the feed (another voyage elsewhere): an unknown vessel is only a data gap
    st.vessel_call_rows.append({**gone[0], "voyage": "999X", "port": "Elsewhere"})
    app = Suraksha(st)
    for r in ds.requests:
        if r.request_id != dup.request_id:
            app.process(r)
    res = app.process(dup)
    assert "R_NO_VESSEL_CALL" in {e.rule_id for e in res.confidence.evidence}
    part5 = next(s for s in res.report.sections if s.part == "PART 5")
    sent = [s for s in part5.sentences if s.text.startswith("Physical-cargo check")]
    assert sent and any(c.ref.startswith("vessel_calls:") for c in sent[0].citations)
    assert not validate_citations(res.report)


def test_unknown_vessel_is_a_gap_not_evidence():
    """Master ITER-06: incomplete AIS coverage must not accuse. A vessel the feed has never seen -> no
    R_NO_VESSEL_CALL, but an analyst ask; a known vessel with no call for this voyage still fires."""
    from datetime import date
    from suraksha.agents import investigator
    from suraksha.config import get_settings
    from suraksha.models import ExtractedFields, FinancingRequest
    from suraksha.store.memory import MemoryStore
    st = MemoryStore()
    st.load_registry([], [], [], [], [], [], [], [
        {"vessel": "Sea Falcon", "voyage": "12E", "port": "Mundra", "arrived": date(2026, 5, 1), "departed": date(2026, 5, 3)}])
    def f(vessel):
        return ExtractedFields(request_id="R", vessel=vessel, voyage="66S", port_of_loading="Mundra",
                               shipment_date=date(2026, 5, 2))
    req = FinancingRequest("R", "BANK_A", "C1", 1.0, "USD", None, [])
    s = get_settings()
    assert investigator.vessel_call_check(f("Never Seen Vessel"), st, s) is None
    assert any("not covered by the port-call feed" in g for g in investigator.document_gaps(req, f("Never Seen Vessel"), st))
    ev = investigator.vessel_call_check(f("Sea Falcon"), st, s)  # known vessel, no call for voyage 66S
    assert ev is not None and ev.rule_id == "R_NO_VESSEL_CALL"
