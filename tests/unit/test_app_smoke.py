"""Smoke test: every persona page of the Streamlit app renders without exception."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("pandas")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py")
PAGES = ["Trade-ops analyst", "Investigator", "Compliance officer (MLRO)", "Risk head", "Policy what-if"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception
    at.sidebar.radio(key="persona").set_value(page).run()
    assert not at.exception, at.exception


def test_investigator_question():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Investigator").run()
    at.text_input(key="inv_q").set_value("directors of C0001").run()
    assert not at.exception, at.exception


def _metric(at, label):
    return next(m for m in at.metric if m.label == label)


def test_whatif_defaults_match_pipeline_and_flips_on_threshold():
    from suraksha.config import RULE_WEIGHTS

    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Policy what-if").run()
    assert not at.exception, at.exception
    assert len(at.slider) >= 1 + len(RULE_WEIGHTS)  # threshold + one per rule (incl. any new rules)
    assert _metric(at, "Detection rate").delta.startswith("+0.0")  # defaults == current policy
    assert any("governed approval" in c.value for c in at.caption)
    at.slider(key="wi_thr").set_value(1.0).run()  # near-impossible bar: almost nothing escalates
    assert not at.exception, at.exception
    assert _metric(at, "Detection rate").delta.startswith("-")
    at.button(key="wi_reset").click().run()
    assert not at.exception, at.exception
    assert _metric(at, "Detection rate").delta.startswith("+0.0")


def test_mlro_evidence_chain_steps():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Compliance officer (MLRO)").run()
    assert not at.exception, at.exception
    labels = [e.label for e in at.expander]
    for n in ("1 ·", "2 ·", "3 ·", "4 ·", "5 ·"):
        assert any(l.startswith(n) for l in labels), labels
    assert any("Decision & audit trail" in m.value for m in at.markdown)


def test_risk_head_operational_metrics():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Risk head").run()
    assert not at.exception, at.exception
    assert any(m.label.startswith("Median time") for m in at.metric)
    assert any(m.label.startswith("Analyst queue") for m in at.metric)


def test_mlro_has_verify_button():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Compliance officer (MLRO)").run()
    assert not at.exception, at.exception
    assert any("Verify" in b.label for b in at.button)


# ------------------------------------------------------------------ Snowflake mode (fake session)
import json  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self.description = []
        self._rows = []

    def execute(self, sql, params=None):
        self.db.calls.append((sql, params))
        for needle, (cols, rows) in self.db.routes:
            if needle in sql:
                if callable(rows):
                    rows = rows(params)
                self.description = [(c,) for c in cols]
                self._rows = rows
                return
        self.description, self._rows = [], []

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeDB:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def cursor(self):
        return _FakeCursor(self)


class _FakeSession:
    def __init__(self, routes):
        self.connection = _FakeDB(routes)


_RES_COLS = ["REQUEST_ID", "STATUS", "PY_SCORE", "PY_BAND", "PY_RULES", "LABEL_DUPLICATE", "BANK_ID", "BORROWER_ID", "AMOUNT",
             "CURRENCY", "SUBMITTED_AT", "COMMODITY", "LABEL_DUPLICATE"]
_CASE_COLS = ["CASE_ID", "REQUEST_ID", "REPORT_ID", "STATUS", "HOLD_RECOMMENDED", "DECIDED_BY", "DECIDED_AT",
              "REASON"]


def _audit_rows():
    """A valid hash chain whose timestamps come back naive (as Snowflake TIMESTAMP_NTZ does)."""
    from suraksha.agents.approval import AuditLog
    from suraksha.store.memory import MemoryStore

    st_ = MemoryStore()
    log = AuditLog(st_)
    log.append("system:intake", "REQUEST_RECEIVED", "R1", {"a": 1})
    log.append("system:pipeline", "EVIDENCE_SCORED", "R1", {"b": 2})
    return [(r.seq, r.at.replace(tzinfo=None), r.actor, r.action, r.subject_id, json.dumps(r.payload),
             r.prev_hash, r.entry_hash) for r in st_.list_audit()]


def _routes(results, decide=None):
    now = datetime(2026, 1, 5, 12, 0, 0)
    res_rows = [("R1", "PENDING_APPROVAL", 0.91, "HIGH", '["R_EXACT_HASH"]', True, "BANK_A", "C0001", 1000.0, "INR",
                 now, "rice"),
                ("R2", "CLEAR", None, None, "[]", False, "BANK_B", "C0002", 500.0, "INR", now, "wheat")]
    return [
        ("PIPELINE_RESULTS", (_RES_COLS, results and res_rows or [])),
        ("CORE.CASES", (_CASE_COLS, [("CASE-R1", "R1", "REP-R1", "PENDING_APPROVAL", False, None, None, None)]
                        if results else [])),
        ("REGISTRY.COMPANIES", (["COMPANY_ID", "NAME", "REG_NO", "ADDRESS_ID", "PHONE", "INCORPORATED", "COUNTRY"],
                                [("C0001", "Alpha Ltd", "X1", "A1", "1", None, "IN"),
                                 ("C0002", "Beta Ltd", "X2", "A2", "2", None, "IN")])),
        ("V_AUDIT_VERIFY", (["BREAKS"], [(0,)])),
        ("AUDIT_LOG", (["SEQ", "AT", "ACTOR", "ACTION", "SUBJECT_ID", "PAYLOAD", "PREV_HASH", "ENTRY_HASH"],
                       _audit_rows())),
        ("DECIDE_CASE", (["DECIDE_CASE"], decide or (lambda p: [(json.dumps({"case_id": p[0], "status": "FILED"}),)]))),
    ]


@pytest.fixture
def fake_snowflake(monkeypatch):
    def install(session):
        pkg = types.ModuleType("snowflake")
        sp = types.ModuleType("snowflake.snowpark")
        ctx = types.ModuleType("snowflake.snowpark.context")
        ctx.get_active_session = lambda: session
        for name, mod in (("snowflake", pkg), ("snowflake.snowpark", sp), ("snowflake.snowpark.context", ctx)):
            monkeypatch.setitem(sys.modules, name, mod)
        import streamlit as st
        st.cache_resource.clear()
    yield install
    import streamlit as st
    st.cache_resource.clear()


def _sidebar_text(at):
    return " ".join(c.value for c in at.sidebar.caption)


def test_snowflake_backend_detected_empty_results(fake_snowflake):
    fake_snowflake(_FakeSession(_routes(results=False)))
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception
    assert "Backend: snowflake" in _sidebar_text(at)
    assert any("RUN_PIPELINE(42)" in i.value for i in at.info)


@pytest.mark.parametrize("page", PAGES)
def test_snowflake_pages_render(fake_snowflake, page):
    fake_snowflake(_FakeSession(_routes(results=True)))
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception
    at.sidebar.radio(key="persona").set_value(page).run()
    assert not at.exception, at.exception
    assert "Backend: snowflake" in _sidebar_text(at)


def test_snowflake_whatif_uses_label_column(fake_snowflake):
    fake_snowflake(_FakeSession(_routes(results=True)))
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Policy what-if").run()
    assert not at.exception, at.exception
    # R1 = labelled duplicate fired R_EXACT_HASH (0.5 < 0.6 threshold) -> LOW at defaults; R2 clean
    assert _metric(at, "Detection rate").value == "0.0%"
    at.slider(key="wi_thr").set_value(0.5).run()
    assert _metric(at, "Detection rate").value == "100.0%"
    assert not [i for i in at.info if "labels are not available" in i.value]


def test_snowflake_verify_and_decide(fake_snowflake):
    session = _FakeSession(_routes(results=True))
    fake_snowflake(session)
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Compliance officer (MLRO)").run()
    assert not at.exception, at.exception
    next(b for b in at.button if "Verify" in b.label).click().run()
    assert not at.exception, at.exception
    assert any("Audit chain intact" in s.value for s in at.success), [s.value for s in at.success]
    # approve routes through DECIDE_CASE, never the in-memory engine
    at.text_input(key="officer_CASE-R1").set_value("A. Officer")
    next(b for b in at.button if "Approve" in b.label).click().run()
    assert not at.exception, at.exception
    calls = [c for c in session.connection.calls if "DECIDE_CASE" in c[0]]
    assert calls and calls[0][1] == ("CASE-R1", "APPROVE", "A. Officer", "")


def test_snowflake_decide_error_shown_verbatim(fake_snowflake):
    def refuse(p):
        raise RuntimeError("system actors may not decide cases")

    fake_snowflake(_FakeSession(_routes(results=True, decide=refuse)))
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Compliance officer (MLRO)").run()
    at.text_input(key="officer_CASE-R1").set_value("system:pipeline")
    next(b for b in at.button if "Approve" in b.label).click().run()
    assert not at.exception, at.exception
    assert any("system actors may not decide cases" in e.value for e in at.error)
