"""Orchestrator: signal -> evidence -> finding.

intake -> fingerprint + consortium match -> investigator -> confidence -> report -> case (pending approval)
Human approval happens separately via `Suraksha.decide` (Streamlit / Slack / CLI).
"""
from __future__ import annotations

import time
from typing import Callable

from suraksha.agents import confidence, fingerprint, intake, investigator, report
from suraksha.agents.approval import AuditLog, decide, open_case
from suraksha.config import Settings, get_settings
from suraksha.log import get_logger
from suraksha.models import (
    Case,
    ConfidenceBand,
    Decision,
    FinancingRequest,
    PipelineResult,
    PipelineStatus,
)

log = get_logger(__name__)

MAX_MATCHES_INVESTIGATED = 3


class Suraksha:
    def __init__(
        self,
        store,
        settings: Settings | None = None,
        notifier: Callable[[Case, object], bool] | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self.audit = AuditLog(store)
        self.notifier = notifier

    def process(self, req: FinancingRequest) -> PipelineResult:
        t0 = time.perf_counter()
        rid = req.request_id
        ctx = {"request_id": rid, "bank_id": req.bank_id}
        self.store.save_request(req)
        self.audit.append("system:intake", "REQUEST_RECEIVED", rid,
                          {"bank_id": req.bank_id, "borrower_id": req.borrower_id, "docs": len(req.documents)})

        # 1. Intake
        fields = intake.extract(req)
        self.store.save_fields(fields)

        # 2. Fingerprint + consortium match (match BEFORE registering our own pledge)
        company = self.store.get_company(req.borrower_id) or {}
        reg_no = company.get("reg_no", req.borrower_id)
        fp = fingerprint.build_fingerprint(req, fields, reg_no, self.settings)
        matches = fingerprint.match(fp, self.store)
        self.store.add_consortium_entry(fingerprint.to_entry(fp, entry_id=f"{req.bank_id}:{rid}"))
        self.audit.append("system:fingerprint", "PLEDGE_REGISTERED", rid,
                          {"matches": [m.entry.entry_id for m in matches]})

        def done(status, inv=None, conf=None, draft=None, case=None) -> PipelineResult:
            ms = (time.perf_counter() - t0) * 1000
            log.info("pipeline_done", extra={"ctx": {**ctx, "status": status.value, "elapsed_ms": round(ms, 1)}})
            return PipelineResult(rid, status, fields, matches, inv, conf, draft, case, ms)

        if not matches:
            self.audit.append("system:pipeline", "CLEARED", rid, {"reason": "no consortium match"})
            return done(PipelineStatus.CLEAR)

        log.info("match_found", extra={"ctx": {**ctx, "n": len(matches), "best": matches[0].match_type.value}})

        # 3 + 4. Investigate the strongest matches; keep the best-scoring one
        best = None
        for m in matches[:MAX_MATCHES_INVESTIGATED]:
            inv = investigator.investigate(req, m, self.store, self.settings)
            conf = confidence.score(inv, self.settings)
            if best is None or conf.score > best[1].score:
                best = (inv, conf)
        inv, conf = best
        self.audit.append("system:investigator", "EVIDENCE_SCORED", rid,
                          {"score": conf.score, "band": conf.band.value,
                           "rules": [e.rule_id for e in conf.evidence],
                           "counterparty_entry": inv.match.entry.entry_id})

        if conf.band == ConfidenceBand.LOW:
            self.audit.append("system:pipeline", "EVIDENCE_REQUESTED", rid, {"missing": conf.missing})
            return done(PipelineStatus.NEED_MORE_EVIDENCE, inv, conf)

        # 5. Report
        draft = report.draft_str(req, fields, inv, conf, self.settings, store=self.store)
        errors = report.validate_citations(draft)
        if errors:  # G3: never hand an officer an uncited claim
            log.error("str_citation_errors", extra={"ctx": {**ctx, "errors": errors}})
            raise RuntimeError(f"STR {draft.report_id} has uncited sentences: {errors}")
        self.store.save_report(draft)

        # 6. Case awaiting a named human
        case = open_case(draft, self.store, self.audit)
        if self.notifier:
            try:
                self.notifier(case, draft)
            except Exception:  # notification must never break detection
                log.exception("notifier_failed", extra={"ctx": ctx})
        return done(PipelineStatus.PENDING_APPROVAL, inv, conf, draft, case)

    def decide(self, case_id: str, decision: Decision, officer: str, reason: str = "") -> Case:
        return decide(case_id, decision, officer, reason, self.store, self.audit)
