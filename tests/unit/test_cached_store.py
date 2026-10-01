"""RegistryCachedStore must give identical pipeline results with far fewer inner-store calls."""
from __future__ import annotations

import collections

from suraksha.models import PipelineStatus
from suraksha.pipeline import Suraksha
from suraksha.store.cached import RegistryCachedStore
from suraksha.store.memory import MemoryStore
from suraksha.synth import generate, load_into


class Counting:
    def __init__(self, inner):
        self._i = inner
        self.calls = collections.Counter()

    def __getattr__(self, n):
        a = getattr(self._i, n)
        if not callable(a):
            return a

        def w(*x, **k):
            self.calls[n] += 1
            return a(*x, **k)
        return w


def _run(store, ds):
    app = Suraksha(store)
    return {r.request_id: (r.status, r.confidence.score if r.confidence else None) for r in map(app.process, ds.requests)}, app


def test_cached_store_same_results_fewer_calls():
    ds = generate(seed=7)
    plain = MemoryStore(); load_into(plain, ds)
    base, _ = _run(plain, ds)

    inner = MemoryStore(); load_into(inner, ds)
    counted = Counting(inner)
    snap = {"companies": ds.companies, "persons": ds.persons, "roles": ds.roles,
            "corp_owners": ds.corp_owners, "addresses": ds.addresses}
    cached, app = _run(RegistryCachedStore(counted, registry=snap), ds)

    assert cached == base
    assert any(s == PipelineStatus.PENDING_APPROVAL for s, _ in cached.values())
    for m in ("get_person", "roles_for_company", "corp_owned_by", "list_companies"):
        assert counted.calls[m] == 0, m
    assert counted.calls["last_audit"] <= 1
    assert app.audit.verify()


def test_cached_store_rejects_non_contiguous_audit():
    import pytest
    from datetime import datetime
    from suraksha.models import AuditRecord
    st = RegistryCachedStore(MemoryStore(), registry={k: [] for k in ("companies", "persons", "roles", "corp_owners", "addresses")})
    rec = AuditRecord(1, datetime(2026, 1, 1), "a", "b", "c", {}, "0" * 64, "x")
    st.append_audit(rec)
    with pytest.raises(ValueError):
        st.append_audit(AuditRecord(3, datetime(2026, 1, 1), "a", "b", "c", {}, "x", "y"))
