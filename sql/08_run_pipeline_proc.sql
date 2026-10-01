-- ============================================================================
-- Suraksha 08_run_pipeline_proc.sql : Snowpark proc SURAKSHA.CORE.RUN_PIPELINE(seed)
-- In-Snowflake end-to-end test: regenerates the synthetic requests (same seed as LOAD_SYNTH),
-- runs every one through suraksha.pipeline.Suraksha over SnowflakeStore (all state lands in
-- Snowflake tables: requests, fields, reports, cases, consortium pledges via SP_PLEDGE_*, audit
-- chain) and returns eval metrics as JSON.
-- Run it ONCE after LOAD_SYNTH: consortium ledger and audit log are append-only (by design),
-- so a second run sees the first run's pledges and every request looks like a duplicate.
-- To reset: DROP DATABASE SURAKSHA and re-run 06 (see docs/SNOWSIGHT_RUNBOOK.md).
-- Runs as the CALLER (SURAKSHA_APP) so the insert-only audit privileges are exercised.
-- Needs sql/07 first (stage @CORE.CODE).
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

-- Pipeline outputs used for the SQL-vs-Python rule parity test (docs/SNOWSIGHT_RUNBOOK.md).
-- INVESTIGATION_FACTS (sql/04) is populated here from each Investigation, so V_CONFIDENCE can
-- recompute the score in pure SQL.  PIPELINE_RESULTS holds what the Python engine decided.
CREATE TABLE IF NOT EXISTS SURAKSHA.CORE.PIPELINE_RESULTS (
  request_id VARCHAR NOT NULL, status VARCHAR, py_score NUMBER(5,4), py_band VARCHAR,
  py_rules ARRAY, label_duplicate BOOLEAN, scenario VARCHAR,
  recorded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
  PRIMARY KEY (request_id)
);
GRANT SELECT, INSERT, UPDATE ON TABLE SURAKSHA.CORE.PIPELINE_RESULTS TO ROLE SURAKSHA_APP;

CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.RUN_PIPELINE(SEED INT)
  RETURNS STRING
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.12'
  PACKAGES = ('snowflake-snowpark-python')
  HANDLER = 'run'
  EXECUTE AS CALLER
AS
$$
import json
import os
import sys
import time
from collections import Counter, defaultdict

STAGES = (  # git clone first: always current after ALTER GIT REPOSITORY ... FETCH (live ITER-04: stale code ran)
    "@SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/",
    "@SURAKSHA.CORE.CODE/src/",
)
DST = "/tmp/suraksha_src"
PROC_VERSION = "iter04-batch-gitfirst"  # bump when the proc body changes; shows in the returned JSON


def ensure_pkg(session):
    # Never trust an already-imported copy: a warm sandbox can keep an old version between calls.
    for m in [m for m in sys.modules if m == "suraksha" or m.startswith("suraksha.")]:
        del sys.modules[m]
    last = None
    for base in STAGES:
        try:
            rows = session.sql("LIST " + base + "suraksha/").collect()
            n = 0
            for r in rows:
                name = r["name"]
                i = name.find("/src/suraksha/")
                if i < 0 or not name.endswith(".py"):
                    continue
                rel = name[i + len("/src/"):]
                tgt = os.path.join(DST, os.path.dirname(rel))
                os.makedirs(tgt, exist_ok=True)
                session.file.get(base + rel, tgt)
                n += 1
            if n:
                if DST not in sys.path:
                    sys.path.insert(0, DST)
                return base + " (" + str(n) + " files)"
        except Exception as e:
            last = e
    raise RuntimeError("could not load suraksha package from stage: " + str(last))


class _Cur:
    def __init__(self, cur):
        self._c = cur

    def execute(self, sql, params=None):
        from suraksha.store.snowflake import inline_nulls  # None would bind as the string 'None'
        sql, params = inline_nulls(sql.replace("%s", "?"), params)  # Snowpark-hosted connector: qmark
        return self._c.execute(sql, params) if params else self._c.execute(sql)

    def executemany(self, sql, rows):
        from suraksha.store.snowflake import batch_values_insert  # multi-row INSERTs (per-row was too slow live)
        rows = [tuple(r) for r in rows]
        stmts = batch_values_insert(sql.replace("%s", "?"), rows)
        if stmts is None:
            for r in rows:
                self.execute(sql, r)
            return
        for s, p in stmts:
            self._c.execute(s, p) if p else self._c.execute(s)

    def fetchall(self):
        return self._c.fetchall()

    @property
    def description(self):
        return self._c.description

    def close(self):
        self._c.close()


class _Conn:
    paramstyle = "qmark"  # Snowpark session connections bind with ? (live ITER-04)
    def __init__(self, session):
        raw = getattr(session, "connection", None)
        if raw is None:
            raw = session._conn._conn  # verify live
        self._raw = raw

    def cursor(self):
        return _Cur(self._raw.cursor())

    def close(self):
        pass


def record(store, ds, res):
    """Persist facts (for V_CONFIDENCE) and the Python verdict (for the parity query)."""
    inv, conf = res.investigation, res.confidence
    if inv is not None and conf is not None:
        same = inv.counterparty_company_id is not None and inv.counterparty_company_id == inv.borrower_id
        own = any(p.edges and all(e.relation == "OWNS" for e in p.edges) for p in inv.paths)
        store._exec(
            "MERGE INTO SURAKSHA.CORE.INVESTIGATION_FACTS t USING (SELECT %s AS request_id, %s AS match_type, "
            "%s AS same_borrower, %s AS u, %s AS d, %s AS own, %s AS a, %s AS ph, %s AS tm) s "
            "ON t.request_id = s.request_id "
            "WHEN MATCHED THEN UPDATE SET match_type = s.match_type, same_borrower = s.same_borrower, "
            "shared_ubo_count = s.u, shared_director_count = s.d, corp_ownership_link = s.own, "
            "shared_address_count = s.a, shared_phone_count = s.ph, timing_overlap_days = s.tm "
            "WHEN NOT MATCHED THEN INSERT (request_id, match_type, same_borrower, shared_ubo_count, "
            "shared_director_count, corp_ownership_link, shared_address_count, shared_phone_count, "
            "timing_overlap_days) VALUES (s.request_id, s.match_type, s.same_borrower, s.u, s.d, s.own, "
            "s.a, s.ph, s.tm)",
            (res.request_id, inv.match.match_type.value, bool(same), len(inv.shared_ubos),
             len(inv.shared_directors), bool(own), len(inv.shared_addresses), len(inv.shared_phones),
             inv.timing_overlap_days))
    store._exec(
        "MERGE INTO SURAKSHA.CORE.PIPELINE_RESULTS t USING (SELECT %s AS request_id, %s AS status, "
        "%s AS sc, %s AS band, PARSE_JSON(%s) AS rules, %s AS lbl, %s AS scn) s "
        "ON t.request_id = s.request_id "
        "WHEN MATCHED THEN UPDATE SET status = s.status, py_score = s.sc, py_band = s.band, "
        "py_rules = s.rules, label_duplicate = s.lbl, scenario = s.scn "
        "WHEN NOT MATCHED THEN INSERT (request_id, status, py_score, py_band, py_rules, label_duplicate, "
        "scenario) VALUES (s.request_id, s.status, s.sc, s.band, s.rules, s.lbl, s.scn)",
        (res.request_id, res.status.value, conf.score if conf else None, conf.band.value if conf else None,
         json.dumps([e.rule_id for e in conf.evidence] if conf else []),
         bool(ds.labels[res.request_id]), ds.scenarios[res.request_id]))


def run(session, seed):
    how = ensure_pkg(session)
    from suraksha.models import PipelineStatus
    from suraksha.pipeline import Suraksha
    from suraksha.store.snowflake import SnowflakeStore
    from suraksha.synth.generator import generate

    ds = generate(seed=int(seed))
    store = SnowflakeStore(_Conn(session))
    engine = Suraksha(store)

    t0 = time.perf_counter()
    results = [engine.process(r) for r in ds.requests]  # in generation order
    elapsed = time.perf_counter() - t0
    for res in results:
        record(store, ds, res)

    tp = fn = fp = tn = 0
    by_status = Counter()
    by_scn = defaultdict(Counter)
    for res in results:
        flagged = res.status != PipelineStatus.CLEAR
        label = ds.labels[res.request_id]
        by_status[res.status.value] += 1
        by_scn[ds.scenarios[res.request_id]][res.status.value] += 1
        if label and flagged:
            tp += 1
        elif label:
            fn += 1
        elif flagged:
            fp += 1
        else:
            tn += 1
    pos, neg = tp + fn, fp + tn
    return json.dumps({
        "seed": int(seed), "package_from": how, "proc_version": PROC_VERSION, "requests": len(results),
        "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "detection_rate": round(tp / pos, 4) if pos else 0.0,
        "false_positive_rate": round(fp / neg, 4) if neg else 0.0,
        "counts_by_status": dict(by_status),
        "by_scenario": {k: dict(v) for k, v in sorted(by_scn.items())},
        "str_drafted": sum(1 for r in results if r.report),
        "elapsed_s": round(elapsed, 1),
        "audit_chain_intact": engine.audit.verify(),
    })
$$;

GRANT USAGE ON PROCEDURE SURAKSHA.CORE.RUN_PIPELINE(INT) TO ROLE SURAKSHA_APP;

-- usage (as SURAKSHA_APP):  CALL SURAKSHA.CORE.RUN_PIPELINE(42);
-- Verify live: SnowflakeStore uses %s binds; the shim loops executemany.  Each request is
-- several round trips; expect minutes, not seconds.
