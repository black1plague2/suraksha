"""Storage contract. Two implementations:
  - store/memory.py    : in-process (tests, local demo, CI)  — no external services
  - store/snowflake.py : Snowflake tables (production / hackathon demo)

OWNER: master (Opus). Agents implement this Protocol; they do not change it.

Registry row shapes (plain dicts; keys are exact column names):
  companies     : company_id, name, reg_no, address_id, phone, incorporated (date), country
  persons       : person_id, name, id_hash
  roles         : company_id, person_id, role ("DIRECTOR"|"SHAREHOLDER"|"UBO"), pct (float|None)
  corp_owners   : owner_company_id, owned_company_id, pct
  addresses     : address_id, line, city, country
  transactions  : txn_id, company_id, bank_id, txn_date (date), amount, currency, counterparty, txn_type
  policy_clauses: clause_id, section, title, text, tags (list[str])
"""
from __future__ import annotations

from typing import Any, Protocol

from suraksha.models import (
    AuditRecord,
    Case,
    ConsortiumEntry,
    ExtractedFields,
    FinancingRequest,
    STRDraft,
)


class Store(Protocol):
    # ---- consortium (shared across banks via Snowflake secure data sharing; hashes only)
    def add_consortium_entry(self, entry: ConsortiumEntry) -> None: ...
    def find_consortium_by_keys(self, keys: dict[str, str], exclude_bank: str) -> list[ConsortiumEntry]:
        """Entries from OTHER banks where ANY key name/value pair equals one in `keys`."""
    def find_consortium_by_band(self, key_name: str, values: list[str], exclude_bank: str) -> list[ConsortiumEntry]:
        """Entries from OTHER banks whose keys[key_name] is in `values` (used for neighbour qty bands)."""
    def list_consortium(self) -> list[ConsortiumEntry]: ...

    # ---- registry (synthetic stand-in for MCA21 / OpenCorporates)
    def get_company(self, company_id: str) -> dict[str, Any] | None: ...
    def list_companies(self) -> list[dict[str, Any]]: ...
    def get_person(self, person_id: str) -> dict[str, Any] | None: ...
    def roles_for_company(self, company_id: str) -> list[dict[str, Any]]: ...
    def roles_for_person(self, person_id: str) -> list[dict[str, Any]]: ...
    def corp_owners_of(self, company_id: str) -> list[dict[str, Any]]:
        """Rows where owned_company_id == company_id."""
    def corp_owned_by(self, company_id: str) -> list[dict[str, Any]]:
        """Rows where owner_company_id == company_id."""
    def get_address(self, address_id: str) -> dict[str, Any] | None: ...

    # ---- bank-private data
    def transactions_for(self, company_id: str, bank_id: str | None = None) -> list[dict[str, Any]]: ...
    def policy_clauses(self, tags: list[str] | None = None) -> list[dict[str, Any]]:
        """All clauses, or those having ANY of `tags`."""

    # ---- workflow persistence
    def save_request(self, req: FinancingRequest) -> None: ...
    def get_request(self, request_id: str) -> FinancingRequest | None: ...
    def save_fields(self, fields: ExtractedFields) -> None: ...
    def save_report(self, report: STRDraft) -> None: ...
    def get_report(self, report_id: str) -> STRDraft | None: ...
    def save_case(self, case: Case) -> None: ...          # upsert by case_id
    def get_case(self, case_id: str) -> Case | None: ...
    def list_cases(self) -> list[Case]: ...

    # ---- audit (append-only; implementations must never update/delete)
    def append_audit(self, record: AuditRecord) -> None: ...
    def list_audit(self, subject_id: str | None = None) -> list[AuditRecord]: ...
    def last_audit(self) -> AuditRecord | None: ...
