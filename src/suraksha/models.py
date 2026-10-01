"""Shared data contracts for every Suraksha agent.

OWNER: master (Opus). Agents must NOT change field names or types here.
If a contract change is genuinely needed, note it in docs/HANDOFF_LOG.md
under "Contract change requests" and let the master apply it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class DocType(str, Enum):
    LC = "LC"
    BILL_OF_LADING = "BL"
    INVOICE = "INVOICE"
    WAREHOUSE_RECEIPT = "WR"


class CitationKind(str, Enum):
    DOCUMENT = "document"        # ref = doc_id, page = page number
    REGISTRY = "registry"        # ref = "<table>:<row_id>", e.g. "companies:C0012"
    POLICY = "policy"            # ref = clause_id, e.g. "TF-4.2"
    CONSORTIUM = "consortium"    # ref = consortium entry_id
    TRANSACTION = "transaction"  # ref = txn_id
    RULE = "rule"                # ref = rule id, e.g. "R_SHARED_UBO"


@dataclass(frozen=True)
class Citation:
    kind: CitationKind
    ref: str
    page: int | None = None
    snippet: str | None = None


# --------------------------------------------------------------------------- intake
@dataclass
class Document:
    doc_id: str
    request_id: str
    doc_type: DocType
    text: str          # plain-text rendering of the PDF (synthetic stand-in); pages split by "\f"
    pages: int = 1


@dataclass
class FinancingRequest:
    request_id: str
    bank_id: str                 # "BANK_A" | "BANK_B" | "BANK_C"
    borrower_id: str             # company_id in the registry
    amount: float
    currency: str
    submitted_at: datetime
    documents: list[Document] = field(default_factory=list)


@dataclass
class ExtractedFields:
    request_id: str
    bl_number: str | None = None
    bl_ref: str | None = None          # B/L number cited by the invoice ("B/L Ref"); differs from bl_number on transshipment
    vessel: str | None = None
    voyage: str | None = None
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    commodity: str | None = None
    quantity: float | None = None
    quantity_unit: str | None = None   # normalised to "MT" where possible
    value: float | None = None
    currency: str | None = None
    shipment_date: date | None = None
    shipper: str | None = None
    consignee: str | None = None
    # field name -> where the value was read from
    sources: dict[str, Citation] = field(default_factory=dict)


# --------------------------------------------------------------------------- fingerprint
@dataclass
class Fingerprint:
    request_id: str
    bank_id: str
    # Named salted hashes. Required keys:
    #   "exact"  : H(bl_norm | vessel | voyage | commodity | qty_band)
    #   "cargo"  : H(vessel | voyage | commodity | qty_band)        -> catches reissued B/L
    #   "bl"     : H(bl_norm | vessel)                              -> catches altered qty/commodity text
    # Agents may add more keys, but these three must exist.
    keys: dict[str, str]
    qty_band: int | None
    borrower_token: str          # H(salt | borrower registration number) — never the name
    created_at: datetime


@dataclass
class ConsortiumEntry:
    """One row of the shared consortium table. Contains NO names, NO amounts."""
    entry_id: str
    bank_id: str
    keys: dict[str, str]
    qty_band: int | None
    borrower_token: str
    pledged_at: datetime


class MatchType(str, Enum):
    EXACT = "EXACT"
    FUZZY = "FUZZY"


@dataclass
class ConsortiumMatch:
    request_id: str
    entry: ConsortiumEntry
    match_type: MatchType
    matched_keys: list[str]      # which fingerprint keys matched, e.g. ["cargo"]
    similarity: float            # 1.0 for EXACT, 0..1 for FUZZY
    citation: Citation


# --------------------------------------------------------------------------- investigation
@dataclass
class GraphEdge:
    src: str           # node id, e.g. "company:C0012" / "person:P0044" / "address:A003" / "phone:+91..."
    dst: str
    relation: str      # DIRECTOR_OF | SHAREHOLDER_OF | UBO_OF | REGISTERED_AT | HAS_PHONE | OWNS
    citation: Citation


@dataclass
class OwnershipPath:
    from_company: str
    to_company: str
    edges: list[GraphEdge]     # ordered path; empty list = no link found


@dataclass
class Evidence:
    rule_id: str               # e.g. "R_EXACT_HASH", "R_SHARED_UBO"
    description: str
    weight: float
    citations: list[Citation]


@dataclass
class Investigation:
    request_id: str
    borrower_id: str
    counterparty_company_id: str | None     # resolved from borrower_token; None if unresolved
    match: ConsortiumMatch
    paths: list[OwnershipPath]
    shared_directors: list[str]             # person ids
    shared_ubos: list[str]                  # person ids
    shared_addresses: list[str]
    shared_phones: list[str]
    transactions: list[dict[str, Any]]      # raw rows from transactions table
    policy_clauses: list[dict[str, Any]]    # rows from policy_clauses table
    timing_overlap_days: int | None


class ConfidenceBand(str, Enum):
    HIGH = "HIGH"      # >= threshold: draft the STR
    LOW = "LOW"        # < threshold: ask analyst for more evidence


@dataclass
class ConfidenceResult:
    request_id: str
    score: float                         # 0..1, sum of evidence weights capped at 1
    band: ConfidenceBand
    evidence: list[Evidence]
    missing: list[str]                   # human-readable asks when band == LOW


# --------------------------------------------------------------------------- report
@dataclass
class ReportSentence:
    text: str
    citations: list[Citation]            # MUST be non-empty


@dataclass
class ReportSection:
    part: str                            # e.g. "PART 3"
    title: str
    sentences: list[ReportSentence]


@dataclass
class STRDraft:
    report_id: str
    request_id: str
    reporting_bank_id: str
    created_at: datetime
    sections: list[ReportSection]
    recommended_action: str              # e.g. "HOLD_DISBURSEMENT"


# --------------------------------------------------------------------------- approval / audit
class Decision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class CaseStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    FILED = "FILED"
    CLOSED = "CLOSED"


@dataclass
class Case:
    case_id: str
    request_id: str
    report_id: str
    status: CaseStatus
    hold_recommended: bool
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None


@dataclass
class AuditRecord:
    seq: int
    at: datetime
    actor: str              # "system:intake", "officer:Priya Nair", ...
    action: str             # e.g. "REQUEST_RECEIVED", "MATCH_FOUND", "CASE_FILED"
    subject_id: str         # request_id / case_id
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str         # sha256(prev_hash + canonical_json(rest)) — tamper-evident chain


# --------------------------------------------------------------------------- pipeline
class PipelineStatus(str, Enum):
    CLEAR = "CLEAR"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    PENDING_APPROVAL = "PENDING_APPROVAL"


@dataclass
class PipelineResult:
    request_id: str
    status: PipelineStatus
    fields: ExtractedFields | None
    matches: list[ConsortiumMatch]
    investigation: Investigation | None
    confidence: ConfidenceResult | None
    report: STRDraft | None
    case: Case | None
    elapsed_ms: float


def to_dict(obj: Any) -> Any:
    """JSON-friendly dict for any dataclass in this module."""
    def conv(v: Any) -> Any:
        if isinstance(v, Enum):
            return v.value
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        return v
    return conv(asdict(obj))
