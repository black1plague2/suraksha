"""Near-real-time screening (ITER-06): screen only the NEW requests, without re-running everything.

Flow inside SURAKSHA.CORE.SCREEN_INBOX (sql/12), triggered by a 1-minute TASK when the stream on
CORE.REQUEST_INBOX has data:

  consume : INSERT ... SELECT FROM <stream>      (a DML read, so the stream offset advances)    1 stmt
  fetch   : SELECT the inbox rows with status = 'NEW'                                           1 stmt
  screen  : screen_requests() = batch.BatchSnowflakeRun read (11 SELECTs) -> the real `Suraksha` pipeline
            in memory over ONLY the new requests -> bulk INSERTs (requests, fields, ledger, reports, cases,
            audit, PIPELINE_RESULTS, INVESTIGATION_FACTS; one statement per table per ~200 rows)
  mark    : one bulk UPDATE ... FROM (SELECT .. UNION ALL ..) setting status/result_status      1 stmt

About 25 statements (~12 s at the 0.5 s/statement seen inside procedures) regardless of how many requests are in
the batch, versus ~11 statements PER request on the per-request path. The audit chain continues the stored
chain (BatchSnowflakeRun pre-loads the last stored record). The inbox `status` column, not the stream, is the
source of truth: a row is never lost; anything that fails is marked ERROR with the message.

Only public functions of store/batch.py and store/snowflake.py are used (BatchSnowflakeRun.read/screen-style
engine access/flush, SnowflakeStore bulk writers) plus SnowflakeStore._query/_exec for the three inbox statements.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from suraksha.log import get_logger
from suraksha.models import DocType, Document, FinancingRequest
from suraksha.store.batch import BatchSnowflakeRun
from suraksha.store.snowflake import BANKS, SnowflakeStore, inline_nulls

log = get_logger(__name__)

T_INBOX = "SURAKSHA.CORE.REQUEST_INBOX"
T_STREAM = "SURAKSHA.CORE.REQUEST_INBOX_STREAM"
T_CONSUMED = "SURAKSHA.CORE.INBOX_CONSUMED"
T_RESULTS = "SURAKSHA.CORE.PIPELINE_RESULTS"
LIVE_SCENARIO = "LIVE"
DEFAULT_LIMIT = 200  # inbox rows per pass; run_inbox loops while a pass comes back full

SQL_CONSUME = (
    f"INSERT INTO {T_CONSUMED} (inbox_id) SELECT inbox_id FROM {T_STREAM} WHERE METADATA$ACTION = 'INSERT'"
)
SQL_FETCH = (
    f"SELECT inbox_id, bank_id, TO_VARCHAR(request) AS request FROM {T_INBOX} "
    "WHERE status = 'NEW' ORDER BY submitted_at, inbox_id LIMIT {limit}"
)
SQL_ALREADY = f"SELECT request_id, status FROM {T_RESULTS} WHERE request_id IN (%s)"

# outcome states
SCREENED, ERROR, ALREADY = "SCREENED", "ERROR", "ALREADY"


# ----------------------------------------------------------------------------- (de)serialisation
def request_to_dict(req: FinancingRequest) -> dict:
    """JSON-able form stored in REQUEST_INBOX.request (VARIANT)."""
    return {
        "request_id": req.request_id, "bank_id": req.bank_id, "borrower_id": req.borrower_id,
        "amount": req.amount, "currency": req.currency, "submitted_at": _iso(req.submitted_at),
        "documents": [{"doc_id": d.doc_id, "request_id": d.request_id, "doc_type": d.doc_type.value,
                       "text": d.text, "pages": d.pages} for d in req.documents],
    }


def request_from_dict(d: dict, bank_id: str | None = None) -> FinancingRequest:
    """Inverse of request_to_dict; `bank_id` (the inbox column, set by SUBMIT_REQUEST) wins over the JSON.
    Raises ValueError/KeyError on a malformed request (the caller marks the inbox row ERROR)."""
    if not isinstance(d, dict):
        raise ValueError("request must be a JSON object")
    rid = d["request_id"]
    if not rid:
        raise ValueError("request_id is empty")
    sub = d.get("submitted_at")
    if isinstance(sub, str):
        sub = datetime.fromisoformat(sub.replace("Z", "+00:00"))
    if sub is None:
        sub = datetime.now(timezone.utc)
    if sub.tzinfo is not None:
        sub = sub.astimezone(timezone.utc).replace(tzinfo=None)
    docs = [Document(doc_id=x["doc_id"], request_id=x.get("request_id", rid), doc_type=DocType(x["doc_type"]),
                     text=x["text"], pages=int(x.get("pages", 1))) for x in (d.get("documents") or [])]
    return FinancingRequest(request_id=str(rid), bank_id=bank_id or d["bank_id"], borrower_id=d["borrower_id"],
                            amount=float(d["amount"]), currency=d["currency"], submitted_at=sub, documents=docs)


def _iso(dt: datetime) -> str:
    return dt.isoformat(sep=" ")


# ----------------------------------------------------------------------------- screening
@dataclass
class Outcome:
    request_id: str
    state: str                       # SCREENED | ERROR | ALREADY
    result_status: str | None = None  # PipelineStatus value, or the earlier PIPELINE_RESULTS status for ALREADY
    error: str | None = None
    result: Any = field(default=None, repr=False)  # the PipelineResult when SCREENED


def _screen_once(sf: Any, requests: list[FinancingRequest], settings: Any, scenario: str) -> list[Outcome]:
    """One read -> screen -> flush pass; raises on any unexpected failure BEFORE anything is written
    (flush only runs after every request screened)."""
    run = BatchSnowflakeRun(sf, settings)
    run.read()
    assert run.engine is not None
    pledged = {e.entry_id for e in run.mem.consortium}
    seen: set[str] = set()
    outcomes: list[Outcome | None] = [None] * len(requests)
    todo: list[tuple[int, FinancingRequest]] = []
    for i, r in enumerate(requests):
        key = f"{r.bank_id}:{r.request_id}"
        if r.bank_id not in BANKS:
            outcomes[i] = Outcome(r.request_id, ERROR, error=f"unknown bank_id {r.bank_id!r}")
        elif run.mem.get_company(r.borrower_id) is None:
            outcomes[i] = Outcome(r.request_id, ERROR, error=f"unknown borrower_id {r.borrower_id!r} (not in registry)")
        elif key in pledged:
            outcomes[i] = Outcome(r.request_id, ALREADY)  # screened by an earlier / concurrent run
        elif key in seen:
            outcomes[i] = Outcome(r.request_id, ERROR, error="duplicate request_id within the same batch")
        else:
            seen.add(key)
            todo.append((i, r))
    t0 = time.perf_counter()
    results = [(i, run.engine.process(r)) for i, r in todo]
    run.timings["screen"] = time.perf_counter() - t0
    if results:
        run.flush([res for _, res in results], {}, {res.request_id: scenario for _, res in results})
    for i, res in results:
        outcomes[i] = Outcome(res.request_id, SCREENED, res.status.value, result=res)
    return [o for o in outcomes if o is not None]


def screen_requests(sf: Any, requests: list[FinancingRequest], *, settings: Any = None,
                    scenario: str = LIVE_SCENARIO) -> list[Outcome]:
    """Screen only `requests` against the stored consortium; returns one Outcome per request, in input order.
    `sf` is a SnowflakeStore (or anything with the same read methods and bulk writers).

    Never raises for a single bad request: if the whole-batch pass fails, each request is retried alone so one
    poison request cannot block the others; a request that still fails becomes an ERROR outcome."""
    if not requests:
        return []
    try:
        return _screen_once(sf, requests, settings, scenario)
    except Exception as e:
        log.exception("incremental_batch_failed", extra={"ctx": {"n": len(requests)}})
        if len(requests) == 1:
            return [Outcome(requests[0].request_id, ERROR, error=f"{type(e).__name__}: {str(e)[:400]}")]
    out: list[Outcome] = []
    for r in requests:
        out.extend(screen_requests(sf, [r], settings=settings, scenario=scenario))
    return out


# ----------------------------------------------------------------------------- inbox glue
def mark_inbox(sf: Any, marks: list[tuple[str, str, str | None, str | None]], chunk: int = 200) -> int:
    """One UPDATE per `chunk` rows: marks = [(inbox_id, status, result_status, error)]. Returns statements issued."""
    n = 0
    for i in range(0, len(marks), chunk):
        selects, params = [], []
        for iid, status, rs, err in marks[i:i + chunk]:
            selects.append("SELECT %s AS inbox_id, %s AS status, %s AS result_status, %s AS error")
            params.extend([iid, status, rs, None if err is None else err[:1000]])
        sql = (f"UPDATE {T_INBOX} t SET status = v.status, screened_at = CURRENT_TIMESTAMP()::TIMESTAMP_NTZ, "
               "result_status = v.result_status, error = v.error FROM ("
               + " UNION ALL ".join(selects) + ") v WHERE t.inbox_id = v.inbox_id")
        sql, p = inline_nulls(sql.replace("%s", "?"), params)  # NULL inlined; never bind None
        sf._exec(sql.replace("?", "%s"), p)
        n += 1
    return n


def run_inbox(sf: SnowflakeStore, *, settings: Any = None, limit: int = DEFAULT_LIMIT, max_passes: int = 5,
              statements: Callable[[], int] | None = None, meta: dict | None = None) -> dict:
    """Body of SCREEN_INBOX: consume the stream, screen every NEW inbox row, mark them. JSON-able summary."""
    t0 = time.perf_counter()
    summary: dict = dict(meta or {})
    counts = {SCREENED: 0, ERROR: 0, ALREADY: 0}
    by_status: dict[str, int] = {}
    passes = 0
    sf._exec(SQL_CONSUME)  # advance the stream offset; the NEW status below is the source of truth
    while passes < max_passes:
        rows = sf._query(SQL_FETCH.format(limit=int(limit)))
        if not rows:
            break
        passes += 1
        marks: list[tuple[str, str, str | None, str | None]] = []
        parsed: list[tuple[str, FinancingRequest]] = []
        for row in rows:
            iid = row["inbox_id"]
            try:
                parsed.append((iid, request_from_dict(json.loads(row["request"]), row["bank_id"])))
            except Exception as e:  # malformed JSON / missing field: ERROR row, never dropped
                marks.append((iid, ERROR, None, f"invalid request: {type(e).__name__}: {str(e)[:300]}"))
                counts[ERROR] += 1
        outcomes = screen_requests(sf, [r for _, r in parsed], settings=settings) if parsed else []
        already = [o.request_id for o in outcomes if o.state == ALREADY]
        prior: dict[str, str] = {}
        if already:  # heal rows whose earlier run screened them but failed before marking
            q = SQL_ALREADY.replace("%s", ", ".join(["%s"] * len(already)))
            prior = {r["request_id"]: r["status"] for r in sf._query(q, tuple(already))}
        for (iid, _), o in zip(parsed, outcomes):
            counts[o.state] += 1
            if o.state == SCREENED:
                by_status[o.result_status or "?"] = by_status.get(o.result_status or "?", 0) + 1
                marks.append((iid, "SCREENED", o.result_status, None))
            elif o.state == ALREADY:
                marks.append((iid, "SCREENED", prior.get(o.request_id), None))
            else:
                marks.append((iid, "ERROR", None, o.error))
        mark_inbox(sf, marks)
        if len(rows) < limit:
            break
    summary.update({
        "passes": passes, "screened": counts[SCREENED], "errors": counts[ERROR], "already_screened": counts[ALREADY],
        "counts_by_status": by_status, "statements_issued": statements() if statements else None,
        "elapsed_s": round(time.perf_counter() - t0, 1),
    })
    return summary


# ----------------------------------------------------------------------------- demo request builders
def _next_id(prefix: str, now: datetime) -> str:
    return f"{prefix}-{now.strftime('%Y%m%d%H%M%S')}"


def _clone_docs(src: FinancingRequest, rid: str) -> list[Document]:
    return [Document(doc_id=f"{rid}-{d.doc_type.value}", request_id=rid, doc_type=d.doc_type, text=d.text,
                     pages=d.pages) for d in src.documents]


def demo_duplicate_request(ds: Any, bank_id: str, now: datetime | None = None,
                           request_id: str | None = None) -> FinancingRequest:
    """A new request re-pledging a cargo already in the ledger: the documents of the seed's first
    `dup_exact_shell` request (a linked shell borrower re-presenting the original's cargo), filed by the
    given bank under a fresh `LIVE-<timestamp>` id. The original and the first duplicate sit at two other banks,
    so whichever bank files this, at least one of them is an other-bank match."""
    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None, microsecond=0)
    if bank_id not in BANKS:
        raise ValueError(f"bank_id must be one of {BANKS}")
    src = next(r for r in ds.requests if ds.scenarios[r.request_id] == "dup_exact_shell")
    rid = request_id or _next_id("LIVE", now)
    return FinancingRequest(rid, bank_id, src.borrower_id, src.amount, src.currency, now, _clone_docs(src, rid))


def demo_clean_request(ds: Any, bank_id: str, now: datetime | None = None,
                       request_id: str | None = None) -> FinancingRequest:
    """A new request that must screen CLEAR: the same borrower and documents as the first clean-scenario request
    of `bank_id`, filed again by that bank (a same-bank re-presentation, which the engine treats as a refinance,
    not cross-bank double financing).  Re-using a real cargo keeps the physical-cargo check (vessel-call feed)
    satisfied; a brand-new cargo would fire R_NO_VESSEL_CALL because the port-call feed has no row for it."""
    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None, microsecond=0)
    if bank_id not in BANKS:
        raise ValueError(f"bank_id must be one of {BANKS}")
    src = next(r for r in ds.requests if ds.scenarios[r.request_id] == "clean" and r.bank_id == bank_id)
    rid = request_id or _next_id("LIVE", now)
    return FinancingRequest(rid, bank_id, src.borrower_id, src.amount, src.currency, now, _clone_docs(src, rid))


__all__ = ["Outcome", "screen_requests", "run_inbox", "mark_inbox", "request_to_dict", "request_from_dict",
           "demo_duplicate_request", "demo_clean_request"]
