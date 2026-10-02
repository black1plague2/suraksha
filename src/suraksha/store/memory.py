"""In-process Store implementation (tests, local demo, CI). No external services."""
from __future__ import annotations

import copy
from typing import Any

from suraksha.store.base import vessel_key, voyage_key
from suraksha.models import (
    AuditRecord,
    Case,
    ConsortiumEntry,
    ExtractedFields,
    FinancingRequest,
    STRDraft,
)


class MemoryStore:
    def __init__(self) -> None:
        self.consortium: list[ConsortiumEntry] = []
        self.companies: dict[str, dict[str, Any]] = {}
        self.persons: dict[str, dict[str, Any]] = {}
        self.roles: list[dict[str, Any]] = []
        self.corp_owners: list[dict[str, Any]] = []
        self.addresses: dict[str, dict[str, Any]] = {}
        self.transactions: list[dict[str, Any]] = []
        self.clauses: list[dict[str, Any]] = []
        self.vessel_call_rows: list[dict[str, Any]] = []
        self.requests: dict[str, FinancingRequest] = {}
        self.fields: dict[str, ExtractedFields] = {}
        self.reports: dict[str, STRDraft] = {}
        self.cases: dict[str, Case] = {}
        self._audit: list[AuditRecord] = []

    # ---- bulk loading helper (used by synth + tests)
    def load_registry(
        self,
        companies: list[dict[str, Any]],
        persons: list[dict[str, Any]],
        roles: list[dict[str, Any]],
        corp_owners: list[dict[str, Any]],
        addresses: list[dict[str, Any]],
        transactions: list[dict[str, Any]],
        policy_clauses: list[dict[str, Any]],
        vessel_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.companies.update({c["company_id"]: c for c in companies})
        self.persons.update({p["person_id"]: p for p in persons})
        self.roles.extend(roles)
        self.corp_owners.extend(corp_owners)
        self.addresses.update({a["address_id"]: a for a in addresses})
        self.transactions.extend(transactions)
        self.clauses.extend(policy_clauses)
        self.vessel_call_rows.extend(vessel_calls or [])

    # ---- consortium
    def add_consortium_entry(self, entry: ConsortiumEntry) -> None:
        self.consortium.append(entry)

    def find_consortium_by_keys(self, keys: dict[str, str], exclude_bank: str) -> list[ConsortiumEntry]:
        out = []
        for e in self.consortium:
            if e.bank_id == exclude_bank:
                continue
            if any(e.keys.get(k) == v for k, v in keys.items()):
                out.append(e)
        return out

    def find_consortium_by_band(self, key_name: str, values: list[str], exclude_bank: str) -> list[ConsortiumEntry]:
        vs = set(values)
        return [e for e in self.consortium if e.bank_id != exclude_bank and e.keys.get(key_name) in vs]

    def list_consortium(self) -> list[ConsortiumEntry]:
        return list(self.consortium)

    # ---- registry
    def get_company(self, company_id: str) -> dict[str, Any] | None:
        return self.companies.get(company_id)

    def list_companies(self) -> list[dict[str, Any]]:
        return list(self.companies.values())

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        return self.persons.get(person_id)

    def roles_for_company(self, company_id: str) -> list[dict[str, Any]]:
        return [r for r in self.roles if r["company_id"] == company_id]

    def roles_for_person(self, person_id: str) -> list[dict[str, Any]]:
        return [r for r in self.roles if r["person_id"] == person_id]

    def corp_owners_of(self, company_id: str) -> list[dict[str, Any]]:
        return [r for r in self.corp_owners if r["owned_company_id"] == company_id]

    def corp_owned_by(self, company_id: str) -> list[dict[str, Any]]:
        return [r for r in self.corp_owners if r["owner_company_id"] == company_id]

    def get_address(self, address_id: str) -> dict[str, Any] | None:
        return self.addresses.get(address_id)

    def vessel_calls(self, vessel: str, voyage: str) -> list[dict[str, Any]]:
        vk, yk = vessel_key(vessel), voyage_key(voyage)
        return [r for r in self.vessel_call_rows
                if vessel_key(r["vessel"]) == vk and voyage_key(r["voyage"]) == yk]

    def has_vessel_call_feed(self) -> bool:
        return bool(self.vessel_call_rows)

    def vessel_in_feed(self, vessel: str) -> bool:
        vk = vessel_key(vessel)
        return any(vessel_key(r["vessel"]) == vk for r in self.vessel_call_rows)

    # ---- bank-private
    def transactions_for(self, company_id: str, bank_id: str | None = None) -> list[dict[str, Any]]:
        return [
            t for t in self.transactions
            if t["company_id"] == company_id and (bank_id is None or t["bank_id"] == bank_id)
        ]

    def policy_clauses(self, tags: list[str] | None = None) -> list[dict[str, Any]]:
        if not tags:
            return list(self.clauses)
        ts = set(tags)
        return [c for c in self.clauses if ts.intersection(c.get("tags", []))]

    # ---- workflow
    def save_request(self, req: FinancingRequest) -> None:
        self.requests[req.request_id] = req

    def get_request(self, request_id: str) -> FinancingRequest | None:
        return self.requests.get(request_id)

    def save_fields(self, fields: ExtractedFields) -> None:
        self.fields[fields.request_id] = fields

    def save_report(self, report: STRDraft) -> None:
        self.reports[report.report_id] = report

    def get_report(self, report_id: str) -> STRDraft | None:
        return self.reports.get(report_id)

    def save_case(self, case: Case) -> None:
        self.cases[case.case_id] = copy.deepcopy(case)

    def get_case(self, case_id: str) -> Case | None:
        c = self.cases.get(case_id)
        return copy.deepcopy(c) if c else None

    def list_cases(self) -> list[Case]:
        return [copy.deepcopy(c) for c in self.cases.values()]

    # ---- audit (append-only)
    def append_audit(self, record: AuditRecord) -> None:
        if self._audit and record.seq != self._audit[-1].seq + 1:
            raise ValueError("audit seq must be contiguous")
        self._audit.append(copy.deepcopy(record))

    def list_audit(self, subject_id: str | None = None) -> list[AuditRecord]:
        return [copy.deepcopy(r) for r in self._audit if subject_id is None or r.subject_id == subject_id]

    def last_audit(self) -> AuditRecord | None:
        return copy.deepcopy(self._audit[-1]) if self._audit else None
