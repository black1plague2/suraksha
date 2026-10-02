-- ============================================================================
-- Suraksha 11_run_pipeline_batch.sql : Snowpark proc SURAKSHA.CORE.RUN_PIPELINE_BATCH(seed, reset)
-- Batch mode of the end-to-end test (ITER-05).  RUN_PIPELINE (sql/08) issues ~1,900 statements and,
-- at ~0.5 s per round-trip inside a procedure, exceeds 20 minutes whatever the warehouse size.
-- This proc issues ~50: bulk read (11 SELECTs) -> screening in memory (the same suraksha.pipeline.Suraksha
-- code over a MemoryStore, audit chain continuing the stored chain) -> bulk write (multi-row INSERT ... SELECT).
-- Results are identical to the per-request path; only the I/O pattern differs.
--
-- TRUST MODEL.  EXECUTE AS OWNER (owner = SURAKSHA_ADMIN, who owns CONSORTIUM.LEDGER).  The proc inserts the
-- ledger rows directly instead of calling SP_PLEDGE_BANK_A/B/C 173 times: it is the trusted batch harness
-- simulating all three banks at once.  The per-bank procedures stay the only write path for live single-request
-- intake (a bank role can only write its own bank id).  Hence: granted to SURAKSHA_ADMIN ONLY.
--
-- DEMO-ONLY RESET.  RESET = TRUE first deletes ALL rows of the demo workflow tables (bank REQUESTS, REQUEST_FIELDS,
-- REPORTS, CASES, PIPELINE_RESULTS, INVESTIGATION_FACTS, CONSORTIUM.LEDGER, AUDIT_LOG) so the synthetic demo is
-- repeatable.  This is destructive and deliberately NOT available to SURAKSHA_APP: the append-only rules on
-- AUDIT_LOG / LEDGER bind the app role, while the owner-run reset exists only because the data is synthetic.
-- Never run it against real data.  Registry and transactions (LOAD_SYNTH output) are NOT touched.
-- RESET = FALSE appends to existing state; if these requests were already processed it stops with an error.
--
-- Owner's-rights notes: no USE statements inside (the proc runs in SURAKSHA.CORE with the owner's role); all
-- object names are fully qualified.  Needs sql/07 (stage @CORE.CODE), sql/04 (INVESTIGATION_FACTS), and
-- LOAD_SYNTH already run.
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

-- Refresh the fallback package copy (live ITER-05: the proc fell back to a stale @CORE.CODE without store/batch.py).
CREATE STAGE IF NOT EXISTS SURAKSHA.CORE.CODE ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');
REMOVE @SURAKSHA.CORE.CODE/src/;
COPY FILES INTO @SURAKSHA.CORE.CODE/src/
  FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/
  PATTERN = '.*[.]py';

CREATE TABLE IF NOT EXISTS SURAKSHA.CORE.PIPELINE_RESULTS (
  request_id VARCHAR NOT NULL, status VARCHAR, py_score NUMBER(5,4), py_band VARCHAR,
  py_rules ARRAY, label_duplicate BOOLEAN, scenario VARCHAR,
  recorded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
  PRIMARY KEY (request_id)
);
GRANT SELECT, INSERT, UPDATE ON TABLE SURAKSHA.CORE.PIPELINE_RESULTS TO ROLE SURAKSHA_APP;

CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.RUN_PIPELINE_BATCH(SEED INT, RESET BOOLEAN)
  RETURNS STRING
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.12'
  PACKAGES = ('snowflake-snowpark-python')
  HANDLER = 'run'
  EXECUTE AS OWNER
AS
$$
import json
import os
import shutil
import sys
import time

STAGES = (  # git clone first: always current after ALTER GIT REPOSITORY ... FETCH
    "@SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/",
    "@SURAKSHA.CORE.CODE/src/",
)
DST = "/tmp/suraksha_src"
REQUIRED_MODULES = ('suraksha/store/batch.py', 'suraksha/store/snowflake.py')  # a source missing these is stale
PROC_VERSION = "iter05-batch-pkgcheck"  # bump when the proc body changes; shows in the returned JSON
_STMTS = [0]  # statements issued through the shim (returned as statements_issued)

DEMO_TABLES = (  # synthetic demo workflow state only; registry + transactions are never touched
    "SURAKSHA.BANK_A.REQUESTS", "SURAKSHA.BANK_B.REQUESTS", "SURAKSHA.BANK_C.REQUESTS",
    "SURAKSHA.CORE.REQUEST_FIELDS", "SURAKSHA.CORE.REPORTS", "SURAKSHA.CORE.CASES",
    "SURAKSHA.CORE.PIPELINE_RESULTS", "SURAKSHA.CORE.INVESTIGATION_FACTS",
    "SURAKSHA.CONSORTIUM.LEDGER", "SURAKSHA.CORE.AUDIT_LOG",
)


def ensure_pkg(session):
    # Never trust an already-imported copy: a warm sandbox can keep an old version between calls.
    for m in [m for m in sys.modules if m == "suraksha" or m.startswith("suraksha.")]:
        del sys.modules[m]
    errors = []
    for base in STAGES:
        try:
            rows = session.sql("LIST " + base + "suraksha/").collect()
            rels = []
            for r in rows:
                name = r["name"]
                i = name.find("/src/suraksha/")
                if i >= 0 and name.endswith(".py"):
                    rels.append(name[i + len("/src/"):])
            missing = [x for x in REQUIRED_MODULES if x not in rels]
            if not rels or missing:  # stale copy (live ITER-05: fell back to a stage without store/batch.py)
                errors.append(base + ": " + str(len(rels)) + " files, missing " + str(missing))
                continue
            shutil.rmtree(DST, ignore_errors=True)  # no leftovers from earlier calls in a warm sandbox
            for rel in rels:
                tgt = os.path.join(DST, os.path.dirname(rel))
                os.makedirs(tgt, exist_ok=True)
                session.file.get(base + rel, tgt)
            if DST not in sys.path:
                sys.path.insert(0, DST)
            return base + " (" + str(len(rels)) + " files)"
        except Exception as e:
            errors.append(base + ": " + type(e).__name__ + ": " + str(e)[:300])
    raise RuntimeError("could not load a current suraksha package; tried -> " + " | ".join(errors))


class _Cur:
    def __init__(self, cur):
        self._c = cur

    def execute(self, sql, params=None):
        from suraksha.store.snowflake import inline_nulls  # None would bind as the string 'None'
        sql, params = inline_nulls(sql.replace("%s", "?"), params)  # Snowpark-hosted connector: qmark
        _STMTS[0] += 1
        return self._c.execute(sql, params) if params else self._c.execute(sql)

    def executemany(self, sql, rows):
        from suraksha.store.snowflake import batch_values_insert
        rows = [tuple(r) for r in rows]
        stmts = batch_values_insert(sql.replace("%s", "?"), rows)
        if stmts is None:
            for r in rows:
                self.execute(sql, r)
            return
        for s, p in stmts:
            _STMTS[0] += 1
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


def reset_demo_tables(conn):
    """DEMO-ONLY: wipe the synthetic workflow tables (10 statements). Only ever called when RESET is TRUE."""
    cur = conn.cursor()
    try:
        for t in DEMO_TABLES:
            cur.execute("DELETE FROM " + t)
    finally:
        cur.close()


def run(session, seed, reset):
    how = ensure_pkg(session)
    from suraksha.store.batch import run_batch
    from suraksha.store.snowflake import SnowflakeStore
    from suraksha.synth.generator import generate

    _STMTS[0] = 0
    ds = generate(seed=int(seed))
    conn = _Conn(session)
    if reset is True:  # TRUE only; NULL / FALSE never delete anything
        reset_demo_tables(conn)
    out = run_batch(SnowflakeStore(conn), ds, statements=lambda: _STMTS[0], meta={
        "seed": int(seed), "package_from": how, "proc_version": PROC_VERSION, "reset": reset is True})
    return json.dumps(out)
$$;

-- ADMIN ONLY: owner's rights + destructive demo reset.  Deliberately NOT granted to SURAKSHA_APP.
GRANT USAGE ON PROCEDURE SURAKSHA.CORE.RUN_PIPELINE_BATCH(INT, BOOLEAN) TO ROLE SURAKSHA_ADMIN;

-- usage (as SURAKSHA_ADMIN):
--   CALL SURAKSHA.CORE.RUN_PIPELINE_BATCH(42, TRUE);    -- repeatable demo run (wipes demo workflow tables first)
--   CALL SURAKSHA.CORE.RUN_PIPELINE_BATCH(42, FALSE);   -- append only; errors if already processed
-- Verify live: LIST / session.file.get / session.connection inside an owner's-rights proc, and the multi-row
-- INSERT ... SELECT ... UNION ALL ... PARSE_JSON(?) statements.
