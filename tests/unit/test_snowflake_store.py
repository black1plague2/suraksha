"""SnowflakeStore / cortex tests against a fake connector (no snowflake libs needed)."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from suraksha.integrations import cortex
from suraksha.models import (
    AuditRecord,
    Case,
    CaseStatus,
    Citation,
    CitationKind,
    ConsortiumEntry,
    DocType,
    Document,
    ExtractedFields,
    FinancingRequest,
    ReportSection,
    ReportSentence,
    STRDraft,
)
from suraksha.store import snowflake as sf
from suraksha.store.snowflake import SnowflakeStore

EVIL = "x'); DROP TABLE users; --"


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        cols, rows = self.conn.responses.pop(0) if self.conn.responses else ([], [])
        self.description = [(c.upper(),) for c in cols]
        self._rows = rows

    def executemany(self, sql, seq):
        self.conn.executed.append((sql, list(seq)))

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.executed: list[tuple] = []
        self.responses: list[tuple[list[str], list[tuple]]] = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass

    def queue(self, cols, rows):
        self.responses.append((cols, rows))


@pytest.fixture
def conn():
    return FakeConn()


@pytest.fixture
def st(conn):
    return SnowflakeStore(conn=conn)


T0 = datetime(2026, 6, 1, 10, 30, 0)


def _no_values_in_sql(conn, *values):
    for sql, _ in conn.executed:
        for v in values:
            assert str(v) not in sql, f"value {v!r} interpolated into SQL: {sql}"


# ------------------------------------------------------------------ parameterisation
def test_lookup_values_are_bound_not_interpolated(st, conn):
    st.get_company(EVIL)
    st.roles_for_company(EVIL)
    st.transactions_for(EVIL, "BANK_A")
    st.transactions_for(EVIL)
    st.find_consortium_by_keys({"exact": EVIL, "cargo": "abc"}, EVIL)
    st.find_consortium_by_band("qty_band", [EVIL, "12"], EVIL)
    st.policy_clauses([EVIL])
    st.get_request(EVIL)
    st.get_case(EVIL)
    st.list_audit(EVIL)
    _no_values_in_sql(conn, EVIL, "abc")
    assert all(p for _, p in conn.executed if "%s" in _), "placeholders present => params present"
    # placeholder count == param count for every statement
    for sql, p in conn.executed:
        assert sql.count("%s") == len(p)


def test_writes_are_parameterised(st, conn):
    req = FinancingRequest(EVIL, "BANK_B", "C1", 1.5, "INR", T0,
                           [Document("D1", EVIL, DocType.LC, "text " + EVIL, 1)])
    st.save_request(req)
    st.save_fields(ExtractedFields(EVIL, bl_number=EVIL, shipment_date=date(2026, 5, 1)))
    st.save_case(Case(EVIL, EVIL, EVIL, CaseStatus.PENDING_APPROVAL, False))
    st.add_consortium_entry(ConsortiumEntry(EVIL, "BANK_C", {"exact": EVIL}, 3, EVIL, T0))
    _no_values_in_sql(conn, EVIL)
    # variant columns go through PARSE_JSON(%s)
    assert any("PARSE_JSON(%s)" in s for s, _ in conn.executed)


def test_unknown_bank_rejected(st):
    with pytest.raises(ValueError):
        st.save_request(FinancingRequest("R", "BANK_Z; DROP", "C", 1.0, "INR", T0))
    with pytest.raises(ValueError):
        st.add_consortium_entry(ConsortiumEntry("E", "BANK_Z", {}, 1, "t", T0))
    assert st.transactions_for("C1", "BANK_Z") == []


def test_pledge_uses_bank_specific_procedure(st, conn):
    st.add_consortium_entry(ConsortiumEntry("E1", "BANK_B", {"exact": "h"}, 7, "tok", T0))
    sql, params = conn.executed[-1]
    assert sql.startswith("CALL SURAKSHA.CONSORTIUM.SP_PLEDGE_BANK_B(")
    assert params[0] == "E1" and json.loads(params[1]) == {"exact": "h"} and params[2] == 7
    assert params[4] == "2026-06-01 10:30:00"


# ------------------------------------------------------------------ row mapping
def test_consortium_row_mapping_and_empty_inputs(st, conn):
    assert st.find_consortium_by_keys({}, "BANK_A") == []
    assert st.find_consortium_by_band("qty_band", [], "BANK_A") == []
    assert conn.executed == []
    conn.queue(
        ["entry_id", "bank_id", "keys", "qty_band", "borrower_token", "pledged_at"],
        [("E1", "BANK_B", '{"exact": "h1", "cargo": "h2"}', 100, "tok", T0)],
    )
    [e] = st.find_consortium_by_keys({"cargo": "h2"}, "BANK_A")
    assert e == ConsortiumEntry("E1", "BANK_B", {"exact": "h1", "cargo": "h2"}, 100, "tok", T0)
    sql, params = conn.executed[-1]
    assert "GET(keys, %s)::STRING = %s" in sql and tuple(params) == ("BANK_A", "cargo", "h2")


def test_policy_clauses_tags_parsed(st, conn):
    conn.queue(["clause_id", "section", "title", "text", "tags"],
               [("TF-3.1", "3", "T", "txt", '["hold", "str_filing"]')])
    [c] = st.policy_clauses(["hold"])
    assert c["tags"] == ["hold", "str_filing"] and c["clause_id"] == "TF-3.1"


def test_case_roundtrip_via_params(st, conn):
    case = Case("CS1", "R1", "RP1", CaseStatus.FILED, True, "officer:Priya", T0, "ok")
    st.save_case(case)
    _, p = conn.executed[-1]
    conn.queue(["case_id", "request_id", "report_id", "status", "hold_recommended", "decided_by", "decided_at", "reason"],
               [(p[0], p[1], p[2], p[3], p[4], p[5], T0, p[7])])
    assert st.get_case("CS1") == case
    conn.queue(["case_id"], [])
    assert st.get_case("nope") is None


def test_report_roundtrip(st, conn):
    cit = Citation(CitationKind.RULE, "R_EXACT_HASH", None, "snip")
    rep = STRDraft("RP1", "R1", "BANK_A", T0,
                   [ReportSection("PART 5", "Grounds", [ReportSentence("Because.", [cit])])], "HOLD_DISBURSEMENT")
    st.save_report(rep)
    _, p = conn.executed[-1]
    conn.queue(["report_id", "request_id", "reporting_bank_id", "created_at", "recommended_action", "sections"],
               [(p[0], p[1], p[2], T0, p[4], p[5])])
    assert st.get_report("RP1") == rep


def test_request_roundtrip(st, conn):
    req = FinancingRequest("R1", "BANK_A", "C1", 10.0, "USD", T0, [Document("D1", "R1", DocType.BILL_OF_LADING, "t", 2)])
    st.save_request(req)
    _, p = conn.executed[-1]
    conn.queue(["request_id", "bank_id", "borrower_id", "amount", "currency", "submitted_at", "documents"],
               [("R1", "BANK_A", "C1", 10.0, "USD", T0, p[6])])
    assert st.get_request("R1") == req


def test_fields_roundtrip(st, conn):
    f = ExtractedFields("R1", bl_number="B1", quantity=5.0, shipment_date=date(2026, 5, 1),
                        sources={"bl_number": Citation(CitationKind.DOCUMENT, "D1", 1, "B/L No: B1")})
    st.save_fields(f)
    _, p = conn.executed[-1]
    conn.queue(
        ["request_id", "bl_number", "vessel", "voyage", "port_of_loading", "port_of_discharge", "commodity",
         "quantity", "quantity_unit", "value", "currency", "shipment_date", "shipper", "consignee", "sources"],
        [("R1", "B1", None, None, None, None, None, 5.0, None, None, None, date(2026, 5, 1), None, None, p[14])],
    )
    assert st.get_fields("R1") == f


# ------------------------------------------------------------------ audit
def _rec(seq, prev="0" * 64):
    return AuditRecord(seq, T0, "system:intake", "REQUEST_RECEIVED", "R1", {"a": 1}, prev, "f" * 64)


def test_audit_insert_only(st, conn):
    st.append_audit(_rec(1))                       # last_audit() -> empty
    st.append_audit.__self__.last_audit()          # read path
    audit_sql = [s for s, _ in conn.executed]
    assert any(s.startswith("INSERT INTO SURAKSHA.CORE.AUDIT_LOG") for s in audit_sql)
    for s in audit_sql:
        assert not re.search(r"\b(UPDATE|DELETE|TRUNCATE|MERGE)\b", s, re.I)


def test_audit_contiguity_and_mapping(st, conn):
    conn.queue(["seq", "at", "actor", "action", "subject_id", "payload", "prev_hash", "entry_hash"],
               [(1, T0, "a", "x", "R1", '{"k": 2}', "0" * 64, "e" * 64)])
    with pytest.raises(ValueError):
        st.append_audit(_rec(5))
    conn.queue(["seq", "at", "actor", "action", "subject_id", "payload", "prev_hash", "entry_hash"],
               [(7, T0, "a", "x", "R1", '{"k": 2}', "0" * 64, "e" * 64)])
    last = st.last_audit()
    assert last == AuditRecord(7, T0, "a", "x", "R1", {"k": 2}, "0" * 64, "e" * 64)


def test_module_never_mutates_audit_log():
    src = Path(sf.__file__).read_text(encoding="utf-8")
    for m in re.finditer(r"(UPDATE\s+(\{|SURAKSHA)|DELETE\s+FROM|TRUNCATE\s+TABLE)[^\n]*", src):
        assert "AUDIT" not in m.group(0).upper(), m.group(0)
    assert not re.search(r"MERGE INTO \{T_AUDIT\}", src)


# ------------------------------------------------------------------ bulk load
def test_load_registry_uses_executemany(st, conn):
    st.load_registry(
        companies=[{"company_id": "C1", "name": "N", "reg_no": "R", "address_id": "A1", "phone": "1",
                    "incorporated": date(2020, 1, 1), "country": "IN"}],
        persons=[{"person_id": "P1", "name": "n", "id_hash": "h"}],
        roles=[{"company_id": "C1", "person_id": "P1", "role": "UBO", "pct": 50.0}],
        corp_owners=[], addresses=[{"address_id": "A1", "line": "l", "city": "c", "country": "IN"}],
        transactions=[{"txn_id": "T1", "company_id": "C1", "bank_id": "BANK_A", "txn_date": date(2026, 1, 1),
                       "amount": 1.0, "currency": "INR", "counterparty": "x", "txn_type": "t"}],
        policy_clauses=[{"clause_id": "TF-1", "section": "1", "title": "t", "text": "x", "tags": ["hold"]}],
    )
    sqls = [s for s, _ in conn.executed]
    assert any("BANK_A.TRANSACTIONS" in s for s in sqls)
    assert not any("BANK_B.TRANSACTIONS" in s for s in sqls)  # no rows for B -> skipped
    assert any("PARSE_JSON(%s)" in s and "POLICY_CLAUSES" in s for s in sqls)


# ------------------------------------------------------------------ cortex
def test_cortex_extract_maps_fields(conn):
    conn.queue(["r"], [(json.dumps({"error": None, "response": {
        "bl_number": "MUND123", "vessel": "MV Star", "quantity": "4,987.5", "quantity_unit": "mt",
        "shipment_date": "01/05/2026", "value": "1,200,000", "currency": "usd"}}),)])
    f = cortex.cortex_extract(conn, "@SURAKSHA.CORE.DOCS/bl_001.pdf")
    assert f.request_id == "bl_001" and f.bl_number == "MUND123" and f.quantity == 4987.5
    assert f.quantity_unit == "MT" and f.shipment_date == date(2026, 5, 1) and f.currency == "USD"
    assert f.sources["bl_number"].kind == CitationKind.DOCUMENT
    sql, params = conn.executed[-1]
    assert params == ("@SURAKSHA.CORE.DOCS", "bl_001.pdf") and "bl_001" not in sql


def test_cortex_errors(conn):
    with pytest.raises(RuntimeError):
        cortex.cortex_complete(None, "x")
    with pytest.raises(ValueError):
        cortex.cortex_extract(conn, "no-stage.pdf")
    conn.queue(["r"], [(json.dumps({"error": "boom", "response": None}),)])
    with pytest.raises(RuntimeError):
        cortex.cortex_extract(conn, "@S.C.D/a.pdf")


def test_cortex_complete_binds_prompt(conn):
    conn.queue(["r"], [("narrative [rule:R_EXACT_HASH]",)])
    out = cortex.cortex_complete(conn, EVIL, model="m1")
    assert out.startswith("narrative")
    sql, params = conn.executed[-1]
    assert params == ("m1", EVIL) and EVIL not in sql


def test_qmark_paramstyle_for_snowpark_session_connections():
    # Live ITER-04: inside a stored procedure the connector binds with `?`; `%s` raised "unexpected '%'".
    qconn = FakeConn()
    qconn.paramstyle = "qmark"
    qs = SnowflakeStore(conn=qconn)
    qs.get_company("C1")
    qs.load_registry([], [], [], [], [{"address_id": "A1", "line": "l", "city": "c", "country": "IN"}], [], [])
    assert qconn.executed, "expected SQL to be executed"
    for sql, _ in qconn.executed:
        assert "%s" not in sql and "?" in sql


def test_pyformat_default_keeps_percent_s(st, conn):
    st.get_company("C1")
    assert "%s" in conn.executed[0][0] and "?" not in conn.executed[0][0]


def test_inline_nulls_replaces_only_none_placeholders():
    from suraksha.store.snowflake import inline_nulls
    sql, params = inline_nulls("INSERT INTO T (a, b, c, d) VALUES (?, ?, ?, ?)", ("C1", "P1", "DIRECTOR", None))
    assert sql == "INSERT INTO T (a, b, c, d) VALUES (?, ?, ?, NULL)" and params == ("C1", "P1", "DIRECTOR")
    sql, params = inline_nulls("SELECT ?, ?", (None, None))
    assert sql == "SELECT NULL, NULL" and params is None
    sql, params = inline_nulls("SELECT ?", (EVIL,))  # values are still bound, never interpolated
    assert sql == "SELECT ?" and params == (EVIL,)
    with pytest.raises(ValueError):
        inline_nulls("SELECT ?", (1, None))


def test_batch_values_insert_chunks_and_nulls():
    from suraksha.store.snowflake import batch_values_insert
    rows = [("C1", "P1", "DIRECTOR", None), ("C2", "P2", "UBO", 51.0), ("C3", "P3", "UBO", EVIL)]
    stmts = batch_values_insert("INSERT INTO R (a, b, c, d) VALUES (?, ?, ?, ?)", rows, chunk=2)
    assert len(stmts) == 2
    s0, p0 = stmts[0]
    assert s0 == "INSERT INTO R (a, b, c, d) VALUES (?, ?, ?, NULL), (?, ?, ?, ?)"
    assert p0 == ("C1", "P1", "DIRECTOR", "C2", "P2", "UBO", 51.0)
    assert stmts[1] == ("INSERT INTO R (a, b, c, d) VALUES (?, ?, ?, ?)", ("C3", "P3", "UBO", EVIL))
    # INSERT ... SELECT (e.g. PARSE_JSON) is not batchable -> caller falls back to per-row
    assert batch_values_insert("INSERT INTO P (a, t) SELECT ?, PARSE_JSON(?)", [("x", "[]")]) is None
