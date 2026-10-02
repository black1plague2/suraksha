"""Batch mode: screen a whole request set with ~50 Snowflake statements instead of ~1,900.

Why: inside a stored procedure every statement costs ~0.5 s round-trip (live ITER-04), so the per-request path
(consortium lookups, MERGEs, audit inserts, SP_PLEDGE calls) made RUN_PIPELINE exceed 20 minutes regardless of
warehouse size. The work itself (screening) is pure CPU and takes seconds in memory.

Shape of a run:
  read   : registry (6 SELECTs, incl. REGISTRY.VESSEL_CALLS), consortium ledger (1), policy clauses (1), transactions (3), last audit row (1)
  screen : load all of that into a MemoryStore, run `Suraksha(memory).process(req)` for every request
           (identical code path and results as the plain in-memory pipeline); the AuditLog continues the
           existing Snowflake chain because the last stored record is pre-loaded into the MemoryStore audit
  write  : diff what the run created and flush it with multi-row INSERT ... SELECT statements (fresh rows only,
           no MERGE): requests per bank schema, fields, ledger, reports, cases, audit, plus the demo outputs
           PIPELINE_RESULTS and INVESTIGATION_FACTS

Trust model: the consortium ledger is written directly (bulk INSERT), not through the per-bank SP_PLEDGE_*
procedures. Only the batch procedure (EXECUTE AS OWNER, owner SURAKSHA_ADMIN who owns the ledger) does this: it is
the trusted harness simulating all three banks at once. Live single-request intake keeps using the per-bank
procedures, where a bank role can only ever write its own bank id.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any, Callable

from suraksha.agents.approval import GENESIS, _canonical, _hash
from suraksha.log import get_logger
from suraksha.models import AuditRecord, PipelineResult, PipelineStatus
from suraksha.pipeline import Suraksha
from suraksha.store.memory import MemoryStore

log = get_logger(__name__)

T_RESULTS = "SURAKSHA.CORE.PIPELINE_RESULTS"
T_FACTS = "SURAKSHA.CORE.INVESTIGATION_FACTS"

SQL_FACTS = (
    f"INSERT INTO {T_FACTS} (request_id, match_type, same_borrower, shared_ubo_count, shared_director_count, "
    "corp_ownership_link, shared_address_count, shared_phone_count, timing_overlap_days, "
    "no_vessel_call, doc_mismatch) "
    "SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
)
SQL_RESULTS = (
    f"INSERT INTO {T_RESULTS} (request_id, status, py_score, py_band, py_rules, label_duplicate, scenario) "
    "SELECT %s, %s, %s, %s, PARSE_JSON(%s), %s, %s"
)


class AlreadyProcessedError(RuntimeError):
    """The ledger already holds pledges for these requests (a previous run); reset the demo tables first."""


def facts_row(res: PipelineResult) -> tuple | None:
    """Same columns/derivations as record() in sql/08 for INVESTIGATION_FACTS.

    With a consortium match: the investigation facts plus the document-rule flags. Stand-alone result (no
    consortium match, document/cargo evidence only; investigation is None): match_type NULL, all link facts
    empty, only no_vessel_call / doc_mismatch set."""
    inv, conf = res.investigation, res.confidence
    if conf is None:
        return None
    fired = {e.rule_id for e in conf.evidence}
    nvc, dm = "R_NO_VESSEL_CALL" in fired, "R_DOC_MISMATCH" in fired
    if inv is None:
        return (res.request_id, None, False, 0, 0, False, 0, 0, None, nvc, dm)
    same = inv.counterparty_company_id is not None and inv.counterparty_company_id == inv.borrower_id
    own = any(p.edges and all(e.relation == "OWNS" for e in p.edges) for p in inv.paths)
    return (res.request_id, inv.match.match_type.value, bool(same), len(inv.shared_ubos), len(inv.shared_directors),
            bool(own), len(inv.shared_addresses), len(inv.shared_phones), inv.timing_overlap_days, nvc, dm)


def result_row(res: PipelineResult, label: bool, scenario: str) -> tuple:
    """Same columns as record() in sql/08 for PIPELINE_RESULTS."""
    conf = res.confidence
    return (res.request_id, res.status.value, conf.score if conf else None, conf.band.value if conf else None,
            json.dumps([e.rule_id for e in conf.evidence] if conf else []), bool(label), scenario)


def verify_continuation(prev: AuditRecord | None, records: list[AuditRecord]) -> bool:
    """Hash-chain check of `records` starting from the existing tail `prev` (None = genesis)."""
    prev_hash = prev.entry_hash if prev else GENESIS
    expected = (prev.seq + 1) if prev else None
    for r in records:
        if expected is not None and r.seq != expected:
            return False
        if r.prev_hash != prev_hash:
            return False
        if r.entry_hash != _hash(prev_hash, _canonical(r.seq, r.at, r.actor, r.action, r.subject_id, r.payload)):
            return False
        prev_hash, expected = r.entry_hash, r.seq + 1
    return True


class BatchSnowflakeRun:
    """Bulk read -> in-memory screening -> bulk write. `sf` is a SnowflakeStore (or anything with the same
    read methods and bulk_insert_* / bulk_exec writers)."""

    def __init__(self, sf: Any, settings: Any = None) -> None:
        self.sf = sf
        self.settings = settings
        self.mem = MemoryStore()
        self.engine: Suraksha | None = None
        self._tail: AuditRecord | None = None
        self._n_ledger = 0
        self.timings = {"read": 0.0, "screen": 0.0, "write": 0.0}
        self.statements_written = 0

    # ---------------------------------------------------------------- (a) read
    def read(self) -> None:
        t0 = time.perf_counter()
        sf, mem = self.sf, self.mem
        reg = sf.snapshot_registry()                                  # 6 SELECTs
        mem.load_registry(reg["companies"], reg["persons"], reg["roles"], reg["corp_owners"], reg["addresses"],
                          sf.all_transactions(),                      # 3 SELECTs (one per bank schema)
                          sf.policy_clauses(),                        # 1 SELECT
                          reg.get("vessel_calls"))                    # port-call feed (6th registry SELECT)
        mem.consortium.extend(sf.list_consortium())                   # 1 SELECT
        self._n_ledger = len(mem.consortium)
        self._tail = sf.last_audit()                                  # 1 SELECT
        if self._tail is not None:
            mem._audit.append(self._tail)  # continue the Snowflake chain: next seq / prev_hash come from here
        self.engine = Suraksha(mem, settings=self.settings)
        self.timings["read"] = time.perf_counter() - t0
        log.info("batch_read", extra={"ctx": {"ledger": self._n_ledger, "audit_tail": self._tail.seq if self._tail else 0}})

    # ---------------------------------------------------------------- (b) screen
    def screen(self, requests: list) -> list[PipelineResult]:
        assert self.engine is not None, "call read() first"
        t0 = time.perf_counter()
        ids = {f"{r.bank_id}:{r.request_id}" for r in requests}
        dup = sorted(ids & {e.entry_id for e in self.mem.consortium})
        if dup:
            raise AlreadyProcessedError(
                f"{len(dup)} request(s) already have ledger pledges (e.g. {dup[0]}); "
                "call RUN_PIPELINE_BATCH(seed, TRUE) to reset the demo tables first")
        results = [self.engine.process(r) for r in requests]
        self.timings["screen"] = time.perf_counter() - t0
        return results

    # ---------------------------------------------------------------- (c) diff + (d) flush
    def new_audit(self) -> list[AuditRecord]:
        floor = self._tail.seq if self._tail else 0
        return [r for r in self.mem.list_audit() if r.seq > floor]

    def chain_intact(self) -> bool:
        return verify_continuation(self._tail, self.new_audit())

    def flush(self, results: list[PipelineResult], labels: dict[str, bool], scenarios: dict[str, str]) -> int:
        t0 = time.perf_counter()
        sf, mem = self.sf, self.mem
        n = 0
        n += sf.bulk_insert_requests(list(mem.requests.values()))
        n += sf.bulk_insert_fields(list(mem.fields.values()))
        n += sf.bulk_insert_ledger(mem.consortium[self._n_ledger:])
        n += sf.bulk_insert_reports(list(mem.reports.values()))
        n += sf.bulk_insert_cases(list(mem.cases.values()))
        n += sf.bulk_insert_audit(self.new_audit())
        n += sf.bulk_exec(SQL_FACTS, [row for row in map(facts_row, results) if row])
        n += sf.bulk_exec(SQL_RESULTS, [result_row(r, labels.get(r.request_id, False), scenarios.get(r.request_id, ""))
                                        for r in results])
        self.statements_written = n
        self.timings["write"] = time.perf_counter() - t0
        return n


def run_batch(sf: Any, ds: Any, *, settings: Any = None, statements: Callable[[], int] | None = None,
              meta: dict | None = None) -> dict:
    """Whole batch run over a SynthDataset; returns the same JSON-able shape as RUN_PIPELINE (sql/08) plus
    `statements_issued` and `elapsed_s` split into read / screen / write."""
    run = BatchSnowflakeRun(sf, settings)
    run.read()
    results = run.screen(ds.requests)
    chain_ok = run.chain_intact()
    run.flush(results, ds.labels, ds.scenarios)

    tp = fn = fp = tn = 0
    by_status: Counter = Counter()
    by_scn: dict = defaultdict(Counter)
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
    out = dict(meta or {})
    out.update({
        "requests": len(results),
        "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "detection_rate": round(tp / pos, 4) if pos else 0.0,
        "false_positive_rate": round(fp / neg, 4) if neg else 0.0,
        "counts_by_status": dict(by_status),
        "by_scenario": {k: dict(v) for k, v in sorted(by_scn.items())},
        "str_drafted": sum(1 for r in results if r.report),
        "statements_issued": statements() if statements else None,
        "elapsed_s": {k: round(v, 1) for k, v in run.timings.items()},
        "audit_chain_intact": chain_ok,
    })
    return out
