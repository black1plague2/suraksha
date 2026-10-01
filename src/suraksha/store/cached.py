"""Read-through snapshot of the (static) registry over any Store, for batch runs inside Snowflake.

Why: a full synthetic run makes ~76k store calls, ~74k of them registry lookups from the ownership-graph walk
(get_person / roles_for_company / corp_owned_by). Cheap in memory, hours as individual Snowflake queries
(live ITER-04). The registry does not change during a run, so it is read once (5 bulk SELECTs) and served
from memory. Everything else — consortium, workflow, audit — still goes to the inner store.

Audit: the run is the only writer, so the last audit record is remembered after the first read instead of
re-SELECTed before every append (~560 queries saved). Not for multi-writer use.
"""
from __future__ import annotations

from typing import Any

from suraksha.log import get_logger
from suraksha.models import AuditRecord
from suraksha.store.memory import MemoryStore

log = get_logger(__name__)

_REGISTRY_READS = (
    "get_company", "list_companies", "get_person", "roles_for_company", "roles_for_person",
    "corp_owners_of", "corp_owned_by", "get_address",
)


class RegistryCachedStore:
    def __init__(self, inner: Any, registry: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._inner = inner
        snap = registry if registry is not None else self._snapshot(inner)
        self._reg = MemoryStore()
        self._reg.load_registry(snap["companies"], snap["persons"], snap["roles"], snap["corp_owners"],
                                snap["addresses"], [], [])
        self._last_audit: AuditRecord | None = None
        self._audit_loaded = False
        log.info("registry_snapshot", extra={"ctx": {k: len(v) for k, v in snap.items()}})

    @staticmethod
    def _snapshot(inner: Any) -> dict[str, list[dict[str, Any]]]:
        if hasattr(inner, "snapshot_registry"):
            return inner.snapshot_registry()
        raise TypeError("inner store has no snapshot_registry(); pass registry= explicitly")

    def __getattr__(self, name: str) -> Any:
        if name in _REGISTRY_READS:
            return getattr(self._reg, name)
        return getattr(self._inner, name)

    # ---- single-writer audit cache
    def last_audit(self) -> AuditRecord | None:
        if not self._audit_loaded:
            self._last_audit = self._inner.last_audit()
            self._audit_loaded = True
        return self._last_audit

    def append_audit(self, record: AuditRecord) -> None:
        last = self.last_audit()
        if last is not None and record.seq != last.seq + 1:
            raise ValueError("audit seq must be contiguous")
        if hasattr(self._inner, "append_audit_unchecked"):
            self._inner.append_audit_unchecked(record)
        else:
            self._inner.append_audit(record)
        self._last_audit = record
