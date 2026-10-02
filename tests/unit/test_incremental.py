"""Near-real-time screening (store/incremental.py + sql/12): results vs plain pipeline, audit chain continuation,
statement budget / clean SQL over a fake qmark connection, inbox glue, and static checks of sql/12."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from suraksha.models import PipelineStatus
from suraksha.pipeline import Suraksha
from suraksha.store.batch import verify_continuation
from suraksha.store.incremental import (
    ALREADY, ERROR, SCREENED, demo_clean_request, demo_duplicate_request, mark_inbox, request_from_dict,
    request_to_dict, run_inbox, screen_requests,
)
from suraksha.store.memory import MemoryStore
from suraksha.store.snowflake import SnowflakeStore
from suraksha.synth import generate, load_into


def _naive(rec):
    return rec.at.astimezone(timezone.utc).replace(tzinfo=None)


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        self._rows, self.description = [], None
        if not sql.lstrip().upper().startswith("SELECT"):
            return
        cols = [c.strip() for c in re.match(r"\s*SELECT\s+(.*?)\s+FROM\s", sql, re.I | re.S).group(1).split(",")]
        table = re.search(r"FROM\s+(\S+)", sql).group(1).upper()
        data = self.conn.table(table)
        self.description = [(c.upper(),) for c in cols]
        self._rows = [tuple(self.conn.cell(d, c) for c in cols) for d in data]

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class BatchConn:
    """Fake qmark connection answering the bulk reads from a SynthDataset; records every statement.
    Self-contained (unknown registry tables read as empty) so it keeps working as the store grows new reads."""
    paramstyle = "qmark"

    def __init__(self, ds, audit_tail=None, ledger=()):
        self.ds, self.audit_tail, self.ledger = ds, audit_tail, list(ledger)
        self.executed: list[tuple] = []

    def cursor(self):
        return _Cursor(self)

    def close(self):
        pass

    def table(self, table):
        ds, t = self.ds, table.split(".")[-1]
        if t == "TRANSACTIONS":
            return [x for x in ds.transactions if x["bank_id"] == table.split(".")[-2]]
        if t == "V_SHARED_LEDGER":
            return self.ledger
        if t == "AUDIT_LOG":
            return [self.audit_tail] if self.audit_tail else []
        return getattr(ds, t.lower(), None) or []

    @staticmethod
    def cell(d, col):
        v = d.get(col) if isinstance(d, dict) else getattr(d, col, None)
        if col == "tags":
            return json.dumps(list(v))
        if col in ("keys", "payload"):
            return json.dumps(v)
        return v


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def ds():
    return generate(seed=42)


@pytest.fixture(scope="module")
def seeded(ds):
    """A MemoryStore after the seed-42 run: the state Snowflake holds after RUN_PIPELINE_BATCH(42, TRUE)."""
    mem = MemoryStore()
    load_into(mem, ds)
    app = Suraksha(mem)
    for r in ds.requests:
        app.process(r)
    return mem


def _tail_row(mem):
    t = mem.list_audit()[-1]
    return {"seq": t.seq, "at": _naive(t), "actor": t.actor, "action": t.action, "subject_id": t.subject_id,
            "payload": t.payload, "prev_hash": t.prev_hash, "entry_hash": t.entry_hash}, t


def _new_requests(ds):
    weak_src = next(r for r in ds.requests if ds.scenarios[r.request_id] == "dup_weak_unlinked")
    clean_borrower = next(r.borrower_id for r in ds.requests if ds.scenarios[r.request_id] == "clean")
    weak = demo_duplicate_request(ds, "BANK_B", NOW, "LIVE-WEAK")
    weak.borrower_id = clean_borrower  # an unrelated borrower re-using the weak case's cargo
    weak.documents = [type(d)(f"LIVE-WEAK-{d.doc_type.value}", "LIVE-WEAK", d.doc_type, d.text, d.pages)
                      for d in weak_src.documents]
    return [demo_duplicate_request(ds, "BANK_A", NOW, "LIVE-DUP"),
            demo_clean_request(ds, "BANK_C", NOW, "LIVE-CLEAN"),
            weak]


def test_a_screen_requests_matches_plain_pipeline_and_continues_chain(ds, seeded):
    reqs = _new_requests(ds)
    # reference: the plain in-memory pipeline over a copy of the pre-run state
    ref_store = MemoryStore()
    load_into(ref_store, ds)
    ref_app = Suraksha(ref_store)
    for r in ds.requests:
        ref_app.process(r)
    ref = {r.request_id: ref_app.process(r) for r in reqs}

    tail_row, tail = _tail_row(seeded)
    conn = BatchConn(ds, audit_tail=tail_row, ledger=list(seeded.consortium))
    out = screen_requests(SnowflakeStore(conn), reqs)

    assert [o.request_id for o in out] == ["LIVE-DUP", "LIVE-CLEAN", "LIVE-WEAK"]
    assert all(o.state == SCREENED for o in out), [o.error for o in out]
    got = {o.request_id: o.result_status for o in out}
    assert got == {k: v.status.value for k, v in ref.items()}
    assert got["LIVE-DUP"] == PipelineStatus.PENDING_APPROVAL.value
    assert got["LIVE-CLEAN"] == PipelineStatus.CLEAR.value
    assert out[0].result.report is not None  # STR drafted for the duplicate

    # audit chain: first written record continues the stored tail
    audit = [(s, p) for s, p in conn.executed if "AUDIT_LOG" in s and s.lstrip().upper().startswith("INSERT")]
    assert audit and audit[0][1][0] == tail.seq + 1 and audit[0][1][6] == tail.entry_hash
    # only the new requests were written (3 request rows -> 3 bank tables), not the whole seed
    ledger_ins = [p for s, p in conn.executed if "CONSORTIUM.LEDGER" in s and s.lstrip().upper().startswith("INSERT")]
    assert len(ledger_ins) == 1 and len(ledger_ins[0]) < 3 * 12  # 3 entries, not 173


def test_b_statement_budget_and_clean_sql(ds, seeded):
    tail_row, _ = _tail_row(seeded)
    conn = BatchConn(ds, audit_tail=tail_row, ledger=list(seeded.consortium))
    screen_requests(SnowflakeStore(conn), _new_requests(ds))
    n = len(conn.executed)
    print(f"incremental screen_requests statements for 3 requests: {n}")
    assert n < 40
    for sql, params in conn.executed:
        assert "%s" not in sql and "None" not in sql and "MERGE" not in sql.upper()
        assert all(p is not None and p != "None" for p in (params or ()))


def test_c_guards_unknown_bank_borrower_and_already_screened(ds, seeded):
    tail_row, _ = _tail_row(seeded)
    dup = demo_duplicate_request(ds, "BANK_A", NOW, "LIVE-G1")
    bad_bank = demo_duplicate_request(ds, "BANK_A", NOW, "LIVE-G2")
    bad_bank.bank_id = "BANK_Z"
    bad_borrower = demo_duplicate_request(ds, "BANK_A", NOW, "LIVE-G3")
    bad_borrower.borrower_id = "NOPE"
    twin = demo_duplicate_request(ds, "BANK_A", NOW, "LIVE-G1")  # same bank:request_id in the same batch
    old = ds.requests[0]  # already in the ledger from the seed run
    conn = BatchConn(ds, audit_tail=tail_row, ledger=list(seeded.consortium))
    out = screen_requests(SnowflakeStore(conn), [dup, bad_bank, bad_borrower, twin, old])
    assert [o.state for o in out] == [SCREENED, ERROR, ERROR, ERROR, ALREADY]
    assert "BANK_Z" in out[1].error and "NOPE" in out[2].error and "duplicate request_id" in out[3].error


def test_d_request_json_roundtrip(ds):
    r = demo_duplicate_request(ds, "BANK_A", NOW, "LIVE-RT")
    d = json.loads(json.dumps(request_to_dict(r)))
    back = request_from_dict(d)
    assert back.request_id == "LIVE-RT" and back.documents[0].text == r.documents[0].text
    assert back.submitted_at == r.submitted_at and back.bank_id == "BANK_A"
    assert request_from_dict(d, "BANK_C").bank_id == "BANK_C"  # the inbox column wins
    with pytest.raises(KeyError):
        request_from_dict({"bank_id": "BANK_A"})


class InboxConn(BatchConn):
    """BatchConn plus the three inbox statements: consume (INSERT), fetch NEW rows, UPDATE marks."""

    def __init__(self, ds, inbox, **kw):
        super().__init__(ds, **kw)
        self.inbox = inbox            # list of (inbox_id, bank_id, request_json)
        self.updates: list[tuple] = []
        self.prior: dict[str, str] = {}

    def cursor(self):
        conn, base = self, _Cursor(self)

        class Cur:
            description = None

            def execute(self, sql, params=None):
                if "REQUEST_INBOX" in sql and sql.lstrip().upper().startswith("SELECT"):
                    conn.executed.append((sql, params))
                    new = [r for r in conn.inbox if r[0] not in conn.marked]
                    self.description = [("INBOX_ID",), ("BANK_ID",), ("REQUEST",)]
                    self._rows = new
                elif "PIPELINE_RESULTS" in sql and sql.lstrip().upper().startswith("SELECT"):
                    conn.executed.append((sql, params))
                    self.description = [("REQUEST_ID",), ("STATUS",)]
                    self._rows = list(conn.prior.items())
                elif sql.lstrip().upper().startswith("UPDATE"):
                    conn.executed.append((sql, params))
                    conn.updates.append((sql, params))
                    for i in range(0, len(params), 4):
                        conn.marked.add(params[i])
                    self._rows = []
                else:
                    base.execute(sql, params)
                    self.description, self._rows = base.description, base.fetchall()

            def fetchall(self):
                return list(self._rows)

            def close(self):
                pass

        return Cur()

    marked: set = set()


def test_e_run_inbox_end_to_end(ds, seeded):
    tail_row, _ = _tail_row(seeded)
    good = demo_duplicate_request(ds, "BANK_A", NOW, "LIVE-I1")
    clean = demo_clean_request(ds, "BANK_C", NOW, "LIVE-I2")
    inbox = [("i1", "BANK_A", json.dumps(request_to_dict(good))),
             ("i2", "BANK_C", json.dumps(request_to_dict(clean))),
             ("i3", "BANK_B", "{not json"),
             ("i4", "BANK_B", json.dumps({"request_id": "X"}))]  # missing fields
    conn = InboxConn(ds, inbox, audit_tail=tail_row, ledger=list(seeded.consortium))
    conn.marked = set()
    out = run_inbox(SnowflakeStore(conn), statements=lambda: len(conn.executed))
    assert out["screened"] == 2 and out["errors"] == 2 and out["passes"] == 1
    assert out["counts_by_status"] == {"PENDING_APPROVAL": 1, "CLEAR": 1}
    assert len(conn.executed) < 40 and out["statements_issued"] == len(conn.executed)
    assert len(conn.updates) == 1  # one bulk UPDATE for all four rows
    sql, params = conn.updates[0]
    assert "NULL" in sql and "%s" not in sql and None not in params and "None" not in params
    assert {"i1", "i2", "i3", "i4"} <= set(params)  # NULLs are inlined, so only non-null values are bound
    assert params.count("SCREENED") == 2 and params.count("ERROR") == 2 and "PENDING_APPROVAL" in params
    first = conn.executed[0][0]
    assert "INBOX_CONSUMED" in first and "REQUEST_INBOX_STREAM" in first and "METADATA$ACTION" in first


def test_f_run_inbox_empty_is_cheap(ds):
    conn = InboxConn(ds, [])
    conn.marked = set()
    out = run_inbox(SnowflakeStore(conn))
    assert out["screened"] == 0 and out["passes"] == 0 and len(conn.executed) == 2  # consume + one empty fetch


def test_g_mark_inbox_inlines_nulls():
    class C:
        paramstyle = "qmark"
        executed: list = []

        def cursor(self):
            c = self

            class Cur:
                def execute(self, s, p=None):
                    c.executed.append((s, p))

                def close(self):
                    pass

            return Cur()

    c = C()
    c.executed = []
    n = mark_inbox(SnowflakeStore(c), [("a", "SCREENED", "CLEAR", None), ("b", "ERROR", None, "boom")])
    assert n == 1
    s, p = c.executed[0]
    assert "UNION ALL" in s and "NULL AS error" in s and "NULL AS result_status" in s
    assert p == ("a", "SCREENED", "CLEAR", "b", "ERROR", "boom")


def test_h_demo_requests_are_new_and_valid(ds):
    d = demo_duplicate_request(ds, "BANK_A", NOW)
    assert d.request_id == "LIVE-20261001120000" and d.bank_id == "BANK_A" and d.documents
    assert all(x.request_id == d.request_id for x in d.documents)
    with pytest.raises(ValueError):
        demo_clean_request(ds, "BANK_Q", NOW)


# ------------------------------------------------------------------------------------------ sql/12 static checks
@pytest.fixture(scope="module")
def sql12():
    return (ROOT / "sql" / "12_streaming.sql").read_text(encoding="utf-8")


def test_sql12_stream_task_and_trigger(sql12):
    assert re.search(r"CREATE STREAM IF NOT EXISTS SURAKSHA\.CORE\.REQUEST_INBOX_STREAM\s+ON TABLE "
                     r"SURAKSHA\.CORE\.REQUEST_INBOX\s+APPEND_ONLY = TRUE", sql12)
    assert re.search(r"CREATE OR REPLACE TASK SURAKSHA\.CORE\.SCREEN_INBOX_TASK\s+WAREHOUSE = SURAKSHA_WH\s+"
                     r"SCHEDULE = '1 MINUTE'\s+WHEN SYSTEM\$STREAM_HAS_DATA\('SURAKSHA\.CORE\.REQUEST_INBOX_STREAM'\)\s+"
                     r"AS\s+CALL SURAKSHA\.CORE\.SCREEN_INBOX\(\)", sql12)
    assert "ALTER TASK SURAKSHA.CORE.SCREEN_INBOX_TASK RESUME" in sql12
    assert "-- ALTER TASK SURAKSHA.CORE.SCREEN_INBOX_TASK SUSPEND" in sql12
    assert "GRANT EXECUTE TASK ON ACCOUNT TO ROLE SURAKSHA_ADMIN" in sql12
    assert sql12.index("USE ROLE ACCOUNTADMIN") < sql12.index("GRANT EXECUTE TASK") < sql12.index("USE ROLE SURAKSHA_ADMIN")


def test_sql12_procs_owner_rights_and_grants(sql12):
    for proc in ("SUBMIT_REQUEST(BANK_ID STRING, REQUEST VARIANT)", "SCREEN_INBOX()", "SUBMIT_DEMO(KIND STRING, BANK_ID STRING)",
                 "SUBMIT_DEMO_DUPLICATE(BANK_ID STRING)", "SUBMIT_DEMO_CLEAN(BANK_ID STRING)"):
        m = re.search(r"CREATE OR REPLACE PROCEDURE SURAKSHA\.CORE\." + re.escape(proc) + r"(.*?)\n\$\$;", sql12, re.S)
        assert m, proc
        assert "EXECUTE AS OWNER" in m.group(1).split("$$")[0], proc
    assert "RUNTIME_VERSION = '3.12'" in sql12
    assert "GRANT USAGE ON PROCEDURE SURAKSHA.CORE.SUBMIT_REQUEST(STRING, VARIANT) TO ROLE SURAKSHA_APP" in sql12
    assert "GRANT USAGE ON PROCEDURE SURAKSHA.CORE.SCREEN_INBOX() TO ROLE SURAKSHA_ADMIN" in sql12
    assert "GRANT USAGE ON PROCEDURE SURAKSHA.CORE.SUBMIT_DEMO_DUPLICATE(STRING) TO ROLE SURAKSHA_ADMIN" in sql12
    assert not re.search(r"SCREEN_INBOX\(\) TO ROLE SURAKSHA_APP|SUBMIT_DEMO\w*\(STRING\) TO ROLE SURAKSHA_APP", sql12)
    assert "BANK_ID NOT IN ('BANK_A', 'BANK_B', 'BANK_C')" in sql12
    assert "GRANT SELECT ON TABLE SURAKSHA.CORE.REQUEST_INBOX TO ROLE SURAKSHA_APP" in sql12
    assert not re.search(r"GRANT\s+[^;]*(INSERT|UPDATE|DELETE)[^;]*REQUEST_INBOX", sql12)
    # shim + package check, and no USE inside the owner's-rights procs
    assert "suraksha/store/incremental.py" in sql12 and "inline_nulls" in sql12 and 'paramstyle = "qmark"' in sql12
    for body in re.findall(r"\$\$(.*?)\$\$", sql12, re.S):
        assert not re.search(r"^\s*USE\s", body, re.M)


def test_sql12_in_deploy_chain():
    chain = (ROOT / "sql" / "06_git_repo.sql").read_text(encoding="utf-8")
    line = "EXECUTE IMMEDIATE FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/sql/12_streaming.sql;"
    assert line in chain and chain.index(line) > chain.index("11_run_pipeline_batch.sql;")
