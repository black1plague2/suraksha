"""Batch mode (store/batch.py + sql/11): statement budget, parity with the in-memory pipeline, audit continuation."""
from __future__ import annotations

import json
import re
from datetime import timezone

import pytest

from suraksha.agents.approval import AuditLog
from suraksha.models import ConsortiumEntry
from suraksha.pipeline import Suraksha
from suraksha.store.batch import AlreadyProcessedError, BatchSnowflakeRun, run_batch
from suraksha.store.memory import MemoryStore
from suraksha.store.snowflake import SnowflakeStore, batch_select_insert
from suraksha.synth import generate, load_into


@pytest.fixture(scope="module")
def ds():
    return generate(seed=42)


class BatchCursor:
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
        data = self.conn.table(table, sql)
        self.description = [(c.upper(),) for c in cols]
        self._rows = [tuple(self.conn.cell(d, c) for c in cols) for d in data]

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class BatchConn:
    """Fake qmark connection answering the bulk reads from a SynthDataset; records every statement."""
    paramstyle = "qmark"

    def __init__(self, ds, audit_tail=None, ledger=()):
        self.ds, self.audit_tail, self.ledger = ds, audit_tail, list(ledger)
        self.executed: list[tuple] = []

    def cursor(self):
        return BatchCursor(self)

    def close(self):
        pass

    def table(self, table, sql):
        ds = self.ds
        t = table.split(".")[-1]
        if t == "TRANSACTIONS":
            bank = table.split(".")[-2]
            return [x for x in ds.transactions if x["bank_id"] == bank]
        if t == "V_SHARED_LEDGER":
            return self.ledger
        if t == "AUDIT_LOG":
            return [self.audit_tail] if self.audit_tail else []
        return {"COMPANIES": ds.companies, "PERSONS": ds.persons, "ROLES": ds.roles, "CORP_OWNERS": ds.corp_owners,
                "ADDRESSES": ds.addresses, "POLICY_CLAUSES": ds.policy_clauses}[t]

    @staticmethod
    def cell(d, col):
        v = d[col] if isinstance(d, dict) else getattr(d, col)
        if col == "tags":
            return json.dumps(list(v))
        if col in ("keys", "payload"):
            return json.dumps(v)
        return v


def _writes(conn):
    return [(s, p) for s, p in conn.executed if not s.lstrip().upper().startswith("SELECT")]


def test_a_statement_budget_and_clean_sql(ds):
    conn = BatchConn(ds)
    out = run_batch(SnowflakeStore(conn), ds, statements=lambda: len(conn.executed))
    n = len(conn.executed)
    print(f"batch statements for seed 42: {n} (reads {n - len(_writes(conn))}, writes {len(_writes(conn))})")
    assert n < 100 and out["statements_issued"] == n
    for sql, params in conn.executed:
        assert "%s" not in sql
        assert "None" not in sql
        assert "MERGE" not in sql.upper() and "CALL" not in sql.upper().split()[0:1]
        assert all(p is not None and p != "None" for p in (params or ()))
    inserted = " ".join(s for s, _ in _writes(conn))
    for t in ("BANK_A.REQUESTS", "REQUEST_FIELDS", "CONSORTIUM.LEDGER", "REPORTS", "CASES", "AUDIT_LOG",
              "PIPELINE_RESULTS", "INVESTIGATION_FACTS"):
        assert t in inserted, t
    assert out["requests"] == len(ds.requests) and out["audit_chain_intact"] is True
    assert set(out["elapsed_s"]) == {"read", "screen", "write"}


def test_b_results_equal_plain_memory_pipeline(ds):
    plain = MemoryStore()
    load_into(plain, ds)
    app = Suraksha(plain)
    base = {}
    for r in ds.requests:
        res = app.process(r)
        base[res.request_id] = (res.status, res.confidence.score if res.confidence else None)

    run = BatchSnowflakeRun(SnowflakeStore(BatchConn(ds)))
    run.read()
    got = {r.request_id: (r.status, r.confidence.score if r.confidence else None) for r in run.screen(ds.requests)}
    assert got == base
    assert len(run.mem.consortium) == len(plain.consortium)


def _naive(rec):
    return rec.at.astimezone(timezone.utc).replace(tzinfo=None)


def test_c_audit_chain_continues_existing(ds):
    pre_store = MemoryStore()
    pre = AuditLog(pre_store)
    for i in range(3):
        pre.append("system:test", "SEED", f"S{i}", {"i": i})
    pre_records = pre_store.list_audit()
    tail = pre_records[-1]
    tail_row = {"seq": tail.seq, "at": _naive(tail), "actor": tail.actor, "action": tail.action,
                "subject_id": tail.subject_id, "payload": tail.payload, "prev_hash": tail.prev_hash,
                "entry_hash": tail.entry_hash}

    conn = BatchConn(ds, audit_tail=tail_row)
    out = run_batch(SnowflakeStore(conn), ds)
    assert out["audit_chain_intact"] is True

    audit_stmts = [(s, p) for s, p in _writes(conn) if "AUDIT_LOG" in s]
    assert audit_stmts
    first_params = audit_stmts[0][1]
    assert first_params[0] == tail.seq + 1          # seq continues
    assert first_params[6] == tail.entry_hash      # prev_hash links to the stored tail

    run = BatchSnowflakeRun(SnowflakeStore(BatchConn(ds, audit_tail=tail_row)))
    run.read()
    run.screen(ds.requests)
    new = run.new_audit()
    assert new[0].seq == tail.seq + 1 and new[0].prev_hash == tail.entry_hash
    assert [r.seq for r in new] == list(range(tail.seq + 1, tail.seq + 1 + len(new)))
    full = MemoryStore()
    for r in pre_records + new:
        full.append_audit(r)
    assert AuditLog(full).verify() is True
    # a tampered continuation is caught
    bad = new[1]
    bad.payload = {"x": 1}
    full2 = MemoryStore()
    for r in pre_records + new:
        full2.append_audit(r)
    assert AuditLog(full2).verify() is False


def test_rerun_without_reset_is_refused(ds):
    r = ds.requests[0]
    e = ConsortiumEntry(f"{r.bank_id}:{r.request_id}", r.bank_id, {"exact": "x"}, 1, "tok", r.submitted_at)
    run = BatchSnowflakeRun(SnowflakeStore(BatchConn(ds, ledger=[e])))
    run.read()
    with pytest.raises(AlreadyProcessedError):
        run.screen(ds.requests)


def test_d_batch_select_insert_chunking_and_nulls():
    sql = "INSERT INTO T (a, b, c) SELECT %s, PARSE_JSON(%s), TO_TIMESTAMP_NTZ(%s)"
    rows = [(i, None if i % 2 else "{}", None if i == 0 else "2026-01-01") for i in range(5)]
    stmts = batch_select_insert(sql, rows, chunk=2)
    assert len(stmts) == 3
    s0, p0 = stmts[0]
    assert s0 == ("INSERT INTO T (a, b, c) SELECT ?, PARSE_JSON(?), TO_TIMESTAMP_NTZ(NULL) "
                  "UNION ALL SELECT ?, PARSE_JSON(NULL), TO_TIMESTAMP_NTZ(?)")
    assert p0 == (0, "{}", 1, "2026-01-01")
    assert stmts[2][0].count("SELECT") == 1 and stmts[2][1] == (4, "{}", "2026-01-01")  # head + 1 row
    for s, p in stmts:
        assert "%s" not in s and (p is None or None not in p)
    assert batch_select_insert("INSERT INTO T (a) VALUES (?)", [(1,)]) is None
    assert batch_select_insert(sql, []) is None
    with pytest.raises(ValueError):
        batch_select_insert(sql, [(1, 2)])
    # all-None row -> no params at all
    s, p = batch_select_insert("INSERT INTO T (a) SELECT ?", [(None,)])[0]
    assert s == "INSERT INTO T (a) SELECT NULL" and p is None


def test_bulk_exec_pyformat_connection_keeps_percent_s():
    class Cur:
        def __init__(self, c): self.c = c
        def execute(self, s, p=None): self.c.executed.append((s, p))
        def close(self): pass

    class Conn:
        def __init__(self): self.executed = []
        def cursor(self): return Cur(self)
        def close(self): pass

    c = Conn()
    SnowflakeStore(conn=c).bulk_exec("INSERT INTO T (a, b) SELECT %s, %s", [(1, None), (2, 3)])
    s, p = c.executed[0]
    assert "?" not in s and s.count("%s") == 3 and p == (1, 2, 3)
