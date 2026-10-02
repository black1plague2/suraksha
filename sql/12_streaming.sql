-- ============================================================================
-- Suraksha 12_streaming.sql : near-real-time screening (ITER-06)
-- A new financing request is screened within ~1 minute of arriving, without re-running everything.
--
--   bank --SUBMIT_REQUEST--> CORE.REQUEST_INBOX --(APPEND_ONLY stream)--> SCREEN_INBOX_TASK (1 MINUTE,
--   only when the stream has data) --> SCREEN_INBOX() --> ledger / reports / cases / audit / PIPELINE_RESULTS
--
-- * REQUEST_INBOX is append-only from the bank side: banks (SURAKSHA_APP) can only SELECT it and call
--   SUBMIT_REQUEST, which validates the bank id and inserts.  Only SCREEN_INBOX (owner) updates status columns.
-- * SCREEN_INBOX screens ONLY the new rows with the real `Suraksha` pipeline in memory (store/incremental.py,
--   built on the batch engine): ~25 statements per pass whatever the batch size, ~12 s at 0.5 s/statement.
--   The audit chain continues the stored chain.  A row is never lost: failures are status = 'ERROR' + message.
-- * TRUST MODEL.  EXECUTE AS OWNER (SURAKSHA_ADMIN owns the ledger): like RUN_PIPELINE_BATCH (sql/11) the proc
--   writes the ledger directly, acting as the trusted screening service for all three banks.  SCREEN_INBOX is
--   granted to SURAKSHA_ADMIN only; SUBMIT_REQUEST to SURAKSHA_APP; the SUBMIT_DEMO* helpers to SURAKSHA_ADMIN.
--   Known limit: with owner's rights SUBMIT_REQUEST cannot tell WHICH bank role is calling, so it validates the
--   bank id value but does not bind it to the caller's role (the per-bank SP_PLEDGE_* procs do).
-- * COST.  The task wakes every minute but only runs the warehouse when the stream has data
--   (SYSTEM$STREAM_HAS_DATA is evaluated in cloud services).  Suspend it when not demoing:
--   ALTER TASK SURAKSHA.CORE.SCREEN_INBOX_TASK SUSPEND;
-- Needs sql/01-11 first (tables, stage @CORE.CODE, PIPELINE_RESULTS, INVESTIGATION_FACTS) and LOAD_SYNTH.
-- ============================================================================
USE ROLE ACCOUNTADMIN;
GRANT EXECUTE TASK ON ACCOUNT TO ROLE SURAKSHA_ADMIN;   -- needed to RESUME / EXECUTE tasks owned by SURAKSHA_ADMIN

USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

-- Refresh the fallback package copy (owner's-rights procs cannot LIST the git stage; ITER-05 lesson).
CREATE STAGE IF NOT EXISTS SURAKSHA.CORE.CODE ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');
REMOVE @SURAKSHA.CORE.CODE/src/;
COPY FILES INTO @SURAKSHA.CORE.CODE/src/
  FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/
  PATTERN = '.*[.]py';

-- ---- A. inbox table, stream, consumed-offsets table
CREATE TABLE IF NOT EXISTS SURAKSHA.CORE.REQUEST_INBOX (
  inbox_id      VARCHAR NOT NULL DEFAULT UUID_STRING(),
  bank_id       VARCHAR NOT NULL,
  request       VARIANT NOT NULL COMMENT 'FinancingRequest incl. documents: request_id, borrower_id, amount, currency, submitted_at, documents[]',
  submitted_at  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
  status        VARCHAR DEFAULT 'NEW' COMMENT 'NEW | SCREENED | ERROR',
  screened_at   TIMESTAMP_NTZ,
  result_status VARCHAR COMMENT 'PipelineStatus: CLEAR | NEED_MORE_EVIDENCE | PENDING_APPROVAL',
  error         VARCHAR,
  PRIMARY KEY (inbox_id)
);
GRANT SELECT ON TABLE SURAKSHA.CORE.REQUEST_INBOX TO ROLE SURAKSHA_APP;   -- read-only for the app; writes go through SUBMIT_REQUEST

-- Append-only stream: it only sees INSERTs, so SCREEN_INBOX's status UPDATEs never re-trigger the task.
CREATE STREAM IF NOT EXISTS SURAKSHA.CORE.REQUEST_INBOX_STREAM
  ON TABLE SURAKSHA.CORE.REQUEST_INBOX
  APPEND_ONLY = TRUE;

-- Reading a stream in a DML advances its offset; this table is the DML target (a small log of what was consumed).
CREATE TABLE IF NOT EXISTS SURAKSHA.CORE.INBOX_CONSUMED (
  inbox_id VARCHAR NOT NULL,
  consumed_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ
);

-- ---- B. SUBMIT_REQUEST: the only write path into the inbox
CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.SUBMIT_REQUEST(BANK_ID STRING, REQUEST VARIANT)
  RETURNS STRING
  LANGUAGE SQL
  EXECUTE AS OWNER
AS
$$
DECLARE
  iid STRING DEFAULT UUID_STRING();
  bad_bank EXCEPTION (-20101, 'bank_id must be BANK_A, BANK_B or BANK_C');
  bad_req  EXCEPTION (-20102, 'request must be an object with request_id, borrower_id, amount, currency, documents');
BEGIN
  IF (BANK_ID IS NULL OR BANK_ID NOT IN ('BANK_A', 'BANK_B', 'BANK_C')) THEN
    RAISE bad_bank;
  END IF;
  IF (REQUEST IS NULL OR NOT IS_OBJECT(REQUEST) OR GET(REQUEST, 'request_id') IS NULL
      OR GET(REQUEST, 'borrower_id') IS NULL OR GET(REQUEST, 'amount') IS NULL
      OR GET(REQUEST, 'currency') IS NULL OR GET(REQUEST, 'documents') IS NULL) THEN
    RAISE bad_req;
  END IF;
  INSERT INTO SURAKSHA.CORE.REQUEST_INBOX (inbox_id, bank_id, request)
    SELECT :iid, :BANK_ID, OBJECT_INSERT(:REQUEST::OBJECT, 'bank_id', :BANK_ID, TRUE);
  RETURN iid;
END;
$$;
GRANT USAGE ON PROCEDURE SURAKSHA.CORE.SUBMIT_REQUEST(STRING, VARIANT) TO ROLE SURAKSHA_APP;

-- ---- C. SCREEN_INBOX: consume the stream, screen the new rows, mark them
CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.SCREEN_INBOX()
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

STAGES = (  # git clone first: always current after ALTER GIT REPOSITORY ... FETCH
    "@SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/",
    "@SURAKSHA.CORE.CODE/src/",
)
DST = "/tmp/suraksha_src"
REQUIRED_MODULES = ('suraksha/store/batch.py', 'suraksha/store/snowflake.py',
                    'suraksha/store/incremental.py')  # a source missing these is stale
PROC_VERSION = "iter06-stream-1"  # bump when the proc body changes; shows in the returned JSON
_STMTS = [0]  # statements issued through the shim (returned as statements_issued)

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

def run(session):
    how = ensure_pkg(session)
    from suraksha.store.incremental import run_inbox
    from suraksha.store.snowflake import SnowflakeStore

    _STMTS[0] = 0
    out = run_inbox(SnowflakeStore(_Conn(session)), statements=lambda: _STMTS[0],
                    meta={"package_from": how, "proc_version": PROC_VERSION})
    return json.dumps(out)
$$;
GRANT USAGE ON PROCEDURE SURAKSHA.CORE.SCREEN_INBOX() TO ROLE SURAKSHA_ADMIN;   -- the task runs as its owner

-- ---- D. the task: every minute, only when the stream has data
CREATE OR REPLACE TASK SURAKSHA.CORE.SCREEN_INBOX_TASK
  WAREHOUSE = SURAKSHA_WH
  SCHEDULE = '1 MINUTE'
  WHEN SYSTEM$STREAM_HAS_DATA('SURAKSHA.CORE.REQUEST_INBOX_STREAM')
AS
  CALL SURAKSHA.CORE.SCREEN_INBOX();
ALTER TASK SURAKSHA.CORE.SCREEN_INBOX_TASK RESUME;
-- cost control on the trial account (suspend when not demoing):
-- ALTER TASK SURAKSHA.CORE.SCREEN_INBOX_TASK SUSPEND;

-- ---- E. demo helpers (ADMIN): submit a synthetic request, then watch it get screened
CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.SUBMIT_DEMO(KIND STRING, BANK_ID STRING)
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

STAGES = (  # git clone first: always current after ALTER GIT REPOSITORY ... FETCH
    "@SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/",
    "@SURAKSHA.CORE.CODE/src/",
)
DST = "/tmp/suraksha_src"
REQUIRED_MODULES = ('suraksha/store/batch.py', 'suraksha/store/snowflake.py',
                    'suraksha/store/incremental.py',
                    'suraksha/synth/generator.py', 'suraksha/synth/templates.py')  # a source missing these is stale
PROC_VERSION = "iter06-demo-1"  # bump when the proc body changes; shows in the returned JSON
_STMTS = [0]  # statements issued through the shim (returned as statements_issued)

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

def run(session, kind, bank_id):
    how = ensure_pkg(session)
    from suraksha.store.incremental import demo_clean_request, demo_duplicate_request, request_to_dict
    from suraksha.synth.generator import generate

    ds = generate(seed=42)  # deterministic: the same registry / cargo as LOAD_SYNTH(42) + RUN_PIPELINE_BATCH(42)
    kind = (kind or "").upper()
    if kind == "DUPLICATE":
        req = demo_duplicate_request(ds, bank_id)
    elif kind == "CLEAN":
        req = demo_clean_request(ds, bank_id)
    else:
        raise ValueError("KIND must be DUPLICATE or CLEAN")
    row = session.sql("CALL SURAKSHA.CORE.SUBMIT_REQUEST(?, PARSE_JSON(?))",
                      params=[bank_id, json.dumps(request_to_dict(req))]).collect()
    return json.dumps({"submitted": req.request_id, "kind": kind, "bank_id": bank_id,
                       "inbox_id": row[0][0], "package_from": how, "proc_version": PROC_VERSION,
                       "next": "within ~1 minute: SELECT * FROM SURAKSHA.CORE.PIPELINE_RESULTS WHERE request_id = '"
                               + req.request_id + "'"})
$$;
CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.SUBMIT_DEMO_DUPLICATE(BANK_ID STRING)
  RETURNS STRING
  LANGUAGE SQL
  EXECUTE AS OWNER
AS
$$
BEGIN
  CALL SURAKSHA.CORE.SUBMIT_DEMO('DUPLICATE', :BANK_ID);
  RETURN 'submitted duplicate; see SURAKSHA.CORE.REQUEST_INBOX';
END;
$$;
CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.SUBMIT_DEMO_CLEAN(BANK_ID STRING)
  RETURNS STRING
  LANGUAGE SQL
  EXECUTE AS OWNER
AS
$$
BEGIN
  CALL SURAKSHA.CORE.SUBMIT_DEMO('CLEAN', :BANK_ID);
  RETURN 'submitted clean; see SURAKSHA.CORE.REQUEST_INBOX';
END;
$$;
GRANT USAGE ON PROCEDURE SURAKSHA.CORE.SUBMIT_DEMO(STRING, STRING) TO ROLE SURAKSHA_ADMIN;
GRANT USAGE ON PROCEDURE SURAKSHA.CORE.SUBMIT_DEMO_DUPLICATE(STRING) TO ROLE SURAKSHA_ADMIN;
GRANT USAGE ON PROCEDURE SURAKSHA.CORE.SUBMIT_DEMO_CLEAN(STRING) TO ROLE SURAKSHA_ADMIN;

-- usage (as SURAKSHA_ADMIN, after LOAD_SYNTH(42) + RUN_PIPELINE_BATCH(42, TRUE)):
--   CALL SURAKSHA.CORE.SUBMIT_DEMO_DUPLICATE('BANK_A');
--   -- ~1 minute later:
--   SELECT * FROM SURAKSHA.CORE.REQUEST_INBOX ORDER BY submitted_at DESC;   -- SCREENED, result_status PENDING_APPROVAL
--   SELECT * FROM SURAKSHA.CORE.PIPELINE_RESULTS WHERE request_id LIKE 'LIVE-%';
--   CALL SURAKSHA.CORE.SCREEN_INBOX();                                       -- manual trigger (no waiting)
--   SELECT * FROM TABLE(INFORMATION_SCHEMA.TASK_HISTORY(TASK_NAME => 'SCREEN_INBOX_TASK')) ORDER BY scheduled_time DESC;
