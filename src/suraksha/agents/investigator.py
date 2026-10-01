"""Investigator agent: resolve the counterparty and build the evidence graph.

Graph nodes: ``company:<id>``, ``person:<id>``, ``address:<id>``, ``phone:<num>``.
Edges keep their registry direction/relation (person -> company DIRECTOR_OF / UBO_OF /
SHAREHOLDER_OF, owner_company -> company OWNS, company -> address REGISTERED_AT,
company -> phone HAS_PHONE) but are traversed as undirected for path finding.
Within an OwnershipPath, edges are in traversal order; an edge's own src/dst may
therefore be reversed relative to the walk direction.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from suraksha.config import Settings
from suraksha.log import get_logger
from suraksha.models import (
    Citation,
    CitationKind,
    ConsortiumMatch,
    FinancingRequest,
    GraphEdge,
    Investigation,
    OwnershipPath,
)
from suraksha.store.base import Store

log = get_logger(__name__)

POLICY_TAGS = ["duplicate_financing", "related_party", "hold", "str_filing"]
MAX_PATHS = 3
_ROLE_RELATION = {"DIRECTOR": "DIRECTOR_OF", "SHAREHOLDER": "SHAREHOLDER_OF", "UBO": "UBO_OF"}


def _borrower_token(reg_no: str, salt: str) -> str:
    """Agreed hash: sha256(salt + "|" + "|".join(parts)) with parts = [reg_no]."""
    return hashlib.sha256((salt + "|" + reg_no).encode()).hexdigest()


def resolve_borrower(token: str, store: Store, salt: str) -> str | None:
    for c in store.list_companies():
        reg = c.get("reg_no")
        if reg and _borrower_token(reg, salt) == token:
            return c["company_id"]
    return None


# --------------------------------------------------------------------------- graph
@dataclass
class Graph:
    adj: dict[str, list[tuple[str, GraphEdge]]] = field(default_factory=dict)

    def add(self, edge: GraphEdge) -> None:
        self.adj.setdefault(edge.src, []).append((edge.dst, edge))
        self.adj.setdefault(edge.dst, []).append((edge.src, edge))


def _reg(table: str, row_id: str, snippet: str) -> Citation:
    return Citation(CitationKind.REGISTRY, f"{table}:{row_id}", snippet=snippet)


def build_graph(store: Store) -> Graph:
    g = Graph()
    names = {c["company_id"]: c.get("name", c["company_id"]) for c in store.list_companies()}
    for c in store.list_companies():
        cid = c["company_id"]
        if c.get("address_id"):
            g.add(GraphEdge(f"company:{cid}", f"address:{c['address_id']}", "REGISTERED_AT",
                            _reg("companies", cid, f"{names[cid]} registered at address {c['address_id']}")))
        if c.get("phone"):
            ph = str(c["phone"]).strip()
            g.add(GraphEdge(f"company:{cid}", f"phone:{ph}", "HAS_PHONE",
                            _reg("companies", cid, f"{names[cid]} lists phone {ph}")))
        for r in store.roles_for_company(cid):
            rel = _ROLE_RELATION.get(r["role"], f"{r['role']}_OF")
            pname = (store.get_person(r["person_id"]) or {}).get("name", r["person_id"])
            g.add(GraphEdge(f"person:{r['person_id']}", f"company:{cid}", rel,
                            _reg("roles", f"{cid}/{r['person_id']}/{r['role']}",
                                 f"{pname} is {r['role']} of {names[cid]}")))
        for o in store.corp_owned_by(cid):
            owned = o["owned_company_id"]
            pct = o.get("pct")
            g.add(GraphEdge(f"company:{cid}", f"company:{owned}", "OWNS",
                            _reg("corp_owners", f"{cid}/{owned}",
                                 f"{names.get(cid, cid)} owns {pct if pct is not None else '?'}% of "
                                 f"{names.get(owned, owned)}")))
    return g


def shortest_paths(
    g: Graph, src: str, dst: str, max_hops: int, limit: int = MAX_PATHS, only_relation: str | None = None
) -> list[list[GraphEdge]]:
    """Up to `limit` distinct (by node sequence) shortest paths, <= max_hops edges."""
    if src == dst or src not in g.adj or dst not in g.adj:
        return []
    dist = {src: 0}
    parents: dict[str, list[tuple[str, GraphEdge]]] = {src: []}
    q = deque([src])
    while q:
        n = q.popleft()
        if dist[n] >= max_hops:
            continue
        for nb, edge in g.adj.get(n, []):
            if only_relation and edge.relation != only_relation:
                continue
            if nb not in dist:
                dist[nb] = dist[n] + 1
                parents[nb] = [(n, edge)]
                q.append(nb)
            elif dist[nb] == dist[n] + 1 and all(p != n for p, _ in parents[nb]):
                parents[nb].append((n, edge))
    if dst not in dist:
        return []
    out: list[list[GraphEdge]] = []

    def back(node: str, acc: list[GraphEdge]) -> None:
        if len(out) >= limit:
            return
        if node == src:
            out.append(list(reversed(acc)))
            return
        for prev, edge in parents[node]:
            back(prev, acc + [edge])

    back(dst, [])
    return out


# --------------------------------------------------------------------------- shared attributes
def _parents(store: Store, company_id: str) -> list[str]:
    return [o["owner_company_id"] for o in store.corp_owners_of(company_id)]


def holders(store: Store, company_id: str, role: str, include_parents: bool = True) -> set[str]:
    """Person ids holding `role` in the company or (optionally) a direct parent holding company."""
    scopes = [company_id] + (_parents(store, company_id) if include_parents else [])
    return {r["person_id"] for s in scopes for r in store.roles_for_company(s) if r["role"] == role}


def _days(a, b) -> int:
    return abs((a - b).days)


def investigate(req: FinancingRequest, match: ConsortiumMatch, store: Store, settings: Settings) -> Investigation:
    borrower = req.borrower_id
    cp = resolve_borrower(match.entry.borrower_token, store, settings.consortium_salt)
    timing = _days(req.submitted_at, match.entry.pledged_at)
    txns = store.transactions_for(borrower, req.bank_id)
    policy = store.policy_clauses(POLICY_TAGS)

    paths: list[OwnershipPath] = []
    sd: list[str] = []
    su: list[str] = []
    sa: list[str] = []
    sp: list[str] = []

    if cp is None:
        log.info("counterparty_unresolved", extra={"ctx": {
            "request_id": req.request_id, "entry_id": match.entry.entry_id, "borrower": borrower}})
    else:
        bco = store.get_company(borrower) or {}
        cco = store.get_company(cp) or {}
        sd = sorted(holders(store, borrower, "DIRECTOR") & holders(store, cp, "DIRECTOR"))
        su = sorted(holders(store, borrower, "UBO") & holders(store, cp, "UBO"))
        if bco.get("address_id") and bco.get("address_id") == cco.get("address_id"):
            sa = [bco["address_id"]]
        bph = str(bco.get("phone") or "").strip()
        if bph and bph == str(cco.get("phone") or "").strip():
            sp = [bph]
        if cp != borrower:
            g = build_graph(store)
            s, d = f"company:{borrower}", f"company:{cp}"
            found = shortest_paths(g, s, d, settings.max_graph_hops)
            # make sure a pure ownership chain is surfaced even if longer than the shortest path
            own = shortest_paths(g, s, d, settings.max_graph_hops, limit=1, only_relation="OWNS")
            seqs = {tuple(id(e) for e in p) for p in found}
            for p in own:
                if tuple(id(e) for e in p) not in seqs:
                    found.append(p)
            paths = [OwnershipPath(borrower, cp, p) for p in found]
        log.info("counterparty_resolved", extra={"ctx": {
            "request_id": req.request_id, "counterparty": cp, "same_entity": cp == borrower,
            "paths": [[f"{e.src}-{e.relation}->{e.dst}" for e in p.edges] for p in paths],
            "shared_directors": sd, "shared_ubos": su, "shared_addresses": sa, "shared_phones": sp,
            "timing_overlap_days": timing}})

    return Investigation(
        request_id=req.request_id,
        borrower_id=borrower,
        counterparty_company_id=cp,
        match=match,
        paths=paths,
        shared_directors=sd,
        shared_ubos=su,
        shared_addresses=sa,
        shared_phones=sp,
        transactions=txns,
        policy_clauses=policy,
        timing_overlap_days=timing,
    )


def describe_edge(e: GraphEdge) -> str:
    return f"{e.src} -{e.relation}-> {e.dst}"
