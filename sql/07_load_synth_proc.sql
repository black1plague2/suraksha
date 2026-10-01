-- ============================================================================
-- Suraksha 07_load_synth_proc.sql : Snowpark proc SURAKSHA.CORE.LOAD_SYNTH(seed)
-- Generates the synthetic dataset (suraksha.synth.generate) and loads registry, persons,
-- roles, corp owners, addresses, per-bank transactions and policy clauses via
-- SnowflakeStore.load_registry -- the same column mapping as scripts/load_synth_to_snowflake.py.
-- Requests are NOT loaded here (the pipeline saves them while processing; see 08).
-- Run as SURAKSHA_ADMIN (TRUNCATE needs table ownership).  Idempotent.
--
-- HOW THE `suraksha` PACKAGE REACHES THE PROCEDURE
--   Docs confirm IMPORTS = ('@<git repo>/branches/main/<file>.py') for ONE file.  They do not
--   show a directory import, and flat file imports collide (five __init__.py files).  So:
--   1) COPY FILES clones src/ from the git repo into the internal stage @CORE.CODE (verify live),
--   2) the handler, on first call, LISTs the stage, GETs every .py into /tmp, adds it to sys.path.
--   If COPY FILES from a git stage is rejected, the handler also tries the git stage directly
--   (session.file.get on a git stage -- verify live).
--   Alternative if a directory import turns out to be supported (verify live): drop the
--   bootstrap and use  IMPORTS = ('@SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/suraksha').
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE SCHEMA SURAKSHA.CORE;

CREATE STAGE IF NOT EXISTS SURAKSHA.CORE.CODE
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
  COMMENT = 'python source copied from the git repo for stored procedures';

COPY FILES INTO @SURAKSHA.CORE.CODE/src/
  FROM @SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/
  PATTERN = '.*[.]py';

CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.LOAD_SYNTH(SEED INT)
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

STAGES = (
    "@SURAKSHA.CORE.CODE/src/",
    "@SURAKSHA.CORE.SURAKSHA_REPO/branches/main/src/",
)
DST = "/tmp/suraksha_src"

TRUNCATE_TABLES = [
    "SURAKSHA.REGISTRY.ADDRESSES", "SURAKSHA.REGISTRY.COMPANIES", "SURAKSHA.REGISTRY.PERSONS",
    "SURAKSHA.REGISTRY.ROLES", "SURAKSHA.REGISTRY.CORP_OWNERS", "SURAKSHA.CORE.POLICY_CLAUSES",
    "SURAKSHA.BANK_A.TRANSACTIONS", "SURAKSHA.BANK_B.TRANSACTIONS", "SURAKSHA.BANK_C.TRANSACTIONS",
]


def ensure_pkg(session):
    try:
        import suraksha  # noqa: F401  (works if the package was provided through IMPORTS)
        return "imports"
    except ImportError:
        pass
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
        except Exception as e:  # try next source
            last = e
    raise RuntimeError("could not load suraksha package from stage: " + str(last))


class _Cur:
    def __init__(self, cur):
        self._c = cur

    def execute(self, sql, params=None):
        from suraksha.store.snowflake import inline_nulls  # None would bind as the string 'None'
        sql, params = inline_nulls(sql.replace("%s", "?"), params)  # Snowpark-hosted connector: qmark
        return self._c.execute(sql, params) if params else self._c.execute(sql)

    def executemany(self, sql, rows):  # loop: portable inside stored procedures
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
    """DB-API shim over the Snowpark session's underlying connector connection."""
    paramstyle = "qmark"  # Snowpark session connections bind with ? (live ITER-04)

    def __init__(self, session):
        raw = getattr(session, "connection", None)
        if raw is None:
            raw = session._conn._conn  # verify live: private attr on older snowpark versions
        self._raw = raw

    def cursor(self):
        return _Cur(self._raw.cursor())

    def close(self):  # the session owns the connection
        pass


def run(session, seed):
    how = ensure_pkg(session)
    from suraksha.store.snowflake import SnowflakeStore
    from suraksha.synth.generator import generate

    ds = generate(seed=int(seed))
    store = SnowflakeStore(_Conn(session))
    for t in TRUNCATE_TABLES:  # constants only
        session.sql("TRUNCATE TABLE IF EXISTS " + t).collect()
    store.load_registry(ds.companies, ds.persons, ds.roles, ds.corp_owners, ds.addresses,
                        ds.transactions, ds.policy_clauses)
    return json.dumps({
        "seed": int(seed), "package_from": how,
        "companies": len(ds.companies), "persons": len(ds.persons), "roles": len(ds.roles),
        "corp_owners": len(ds.corp_owners), "addresses": len(ds.addresses),
        "transactions": len(ds.transactions), "policy_clauses": len(ds.policy_clauses),
        "requests_generated_not_loaded": len(ds.requests),
    })
$$;

-- LOAD_SYNTH TRUNCATEs registry/policy tables: SURAKSHA_ADMIN only (owner).  Never grant it to SURAKSHA_APP.
-- (REVOKE guards against a grant left over from an earlier deploy; a 'not granted' notice is fine)
REVOKE USAGE ON PROCEDURE SURAKSHA.CORE.LOAD_SYNTH(INT) FROM ROLE SURAKSHA_APP;
GRANT READ ON STAGE SURAKSHA.CORE.CODE TO ROLE SURAKSHA_APP;
GRANT READ ON STAGE SURAKSHA.CORE.CODE TO ROLE SURAKSHA_AUDITOR;

-- usage (as SURAKSHA_ADMIN):  CALL SURAKSHA.CORE.LOAD_SYNTH(42);
