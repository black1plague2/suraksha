"""Central configuration. Values can be overridden by environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # Consortium-wide salt. In production each consortium agrees this out of band;
    # it is never stored in the shared table.
    consortium_salt: str = field(default_factory=lambda: os.getenv("SURAKSHA_SALT", "dev-only-salt-change-me"))
    # Quantity band width in metric tonnes for fingerprinting (qty_band = round(qty / width)).
    qty_band_mt: float = float(os.getenv("SURAKSHA_QTY_BAND_MT", "50"))
    # Confidence threshold: score >= threshold -> HIGH -> draft STR.
    confidence_threshold: float = float(os.getenv("SURAKSHA_CONFIDENCE_THRESHOLD", "0.6"))
    # Two pledges of the same cargo within this many days count as timing overlap.
    timing_window_days: int = int(os.getenv("SURAKSHA_TIMING_WINDOW_DAYS", "45"))
    # Physical-cargo check (R_NO_VESSEL_CALL): a port call counts when the shipment date is within this many
    # days of the call window [arrived, departed].
    vessel_call_window_days: int = int(os.getenv("SURAKSHA_VESSEL_CALL_WINDOW_DAYS", "10"))
    # Cross-document consistency (R_DOC_MISMATCH) tolerances.
    qty_tolerance: float = float(os.getenv("SURAKSHA_QTY_TOLERANCE", "0.02"))      # relative, after unit normalisation
    value_tolerance: float = float(os.getenv("SURAKSHA_VALUE_TOLERANCE", "0.05"))  # invoice may exceed LC by this much
    # Stand-alone (NO consortium match) document/cargo evidence at or above this score -> NEED_MORE_EVIDENCE.
    # Never HIGH / never an auto-drafted STR without a consortium match.
    standalone_review_threshold: float = float(os.getenv("SURAKSHA_STANDALONE_THRESHOLD", "0.25"))
    # Max hops for the ownership-graph search.
    max_graph_hops: int = int(os.getenv("SURAKSHA_MAX_GRAPH_HOPS", "4"))
    # Backend: "memory" or "snowflake"
    backend: str = field(default_factory=lambda: os.getenv("SURAKSHA_BACKEND", "memory"))
    # Reporting entity details for the STR
    reporting_entity: str = os.getenv("SURAKSHA_REPORTING_ENTITY", "Bank A (synthetic)")
    principal_officer: str = os.getenv("SURAKSHA_PRINCIPAL_OFFICER", "Principal Officer (synthetic)")
    slack_webhook_url: str | None = field(default_factory=lambda: os.getenv("SURAKSHA_SLACK_WEBHOOK"))
    slack_signing_secret: str | None = field(default_factory=lambda: os.getenv("SURAKSHA_SLACK_SIGNING_SECRET"))


# Rule weights used by agents/confidence.py. Deterministic, documented, no black box.
RULE_WEIGHTS: dict[str, float] = {
    "R_EXACT_HASH": 0.50,       # identical cargo fingerprint pledged at another bank
    "R_FUZZY_MATCH": 0.30,      # near-duplicate (reissued B/L, rounded qty)
    "R_SHARED_UBO": 0.30,       # borrowers share an ultimate beneficial owner
    "R_SHARED_DIRECTOR": 0.20,  # borrowers share a director
    "R_CORP_OWNERSHIP": 0.25,   # one borrower owns (directly/indirectly) the other
    "R_SAME_ADDRESS": 0.10,
    "R_SAME_PHONE": 0.10,
    "R_TIMING_OVERLAP": 0.15,   # second pledge within timing window of the first
    "R_NO_VESSEL_CALL": 0.35,   # B/L vessel+voyage has no port call at the POL within +-10d (cargo may not exist)
    "R_DOC_MISMATCH": 0.25,     # B/L vs invoice vs LC vs warehouse receipt disagree (qty / commodity / value)
}


def get_settings() -> Settings:
    return Settings()
