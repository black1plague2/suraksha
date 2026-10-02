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


# =========================================================================== document / cargo checks
# Two deterministic rule families that need NO consortium match (they can raise a request on their own):
#   R_NO_VESSEL_CALL : physical-cargo check against REGISTRY.VESSEL_CALLS (synthetic stand-in for Snowflake
#                      Marketplace AIS / port-call data)
#   R_DOC_MISMATCH   : B/L vs invoice vs LC vs warehouse receipt consistency
# `document_checks` returns fired Evidence; `document_gaps` returns analyst asks for checks that could not run.
# Mirrored in SQL through INVESTIGATION_FACTS.no_vessel_call / doc_mismatch (sql/04).
from datetime import timedelta  # noqa: E402

from suraksha.agents import intake as _intake  # noqa: E402
from suraksha.config import RULE_WEIGHTS  # noqa: E402
from suraksha.models import DocType, Document, Evidence, ExtractedFields  # noqa: E402
from suraksha.store.base import port_key  # noqa: E402


def _dedupe_cites(cs: list[Citation]) -> list[Citation]:
    seen, out = set(), []
    for c in cs:
        k = (c.kind, c.ref, c.page)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def vessel_call_check(fields: ExtractedFields, store: Store, settings: Settings) -> Evidence | None:
    """R_NO_VESSEL_CALL: no call of the B/L vessel+voyage at the port of loading within +-N days of the
    shipment date. Fires only when vessel, voyage, port of loading and shipment date were all extracted and the
    port-call feed is loaded; otherwise None (see `document_gaps`)."""
    if not (fields.vessel and fields.voyage and fields.port_of_loading and fields.shipment_date):
        return None
    has_feed = getattr(store, "has_vessel_call_feed", None)
    if has_feed is not None and not has_feed():
        return None
    win = timedelta(days=settings.vessel_call_window_days)
    ship = fields.shipment_date
    pol = port_key(fields.port_of_loading)
    calls = store.vessel_calls(fields.vessel, fields.voyage)
    in_feed = getattr(store, "vessel_in_feed", None)
    if not calls and in_feed is not None and not in_feed(fields.vessel):
        # Feed never saw this vessel at all: incomplete AIS coverage, not proof the cargo is fake (master, ITER-06).
        return None  # -> document_gaps asks the analyst to verify with the carrier / AIS provider
    for c in calls:
        if port_key(c["port"]) == pol and c["arrived"] - win <= ship <= c["departed"] + win:
            return None  # a matching call exists: cargo plausibly loaded
    scope = (f"vessel {fields.vessel!r} voyage {fields.voyage!r}, port {fields.port_of_loading!r}, "
             f"window {ship - win} .. {ship + win} (shipment date {ship}, +-{settings.vessel_call_window_days} days)")
    if calls:
        seen = "; ".join(f"{c['port']} {c['arrived']}..{c['departed']}" for c in calls)
        desc = (f"No port call at the port of loading within +-{settings.vessel_call_window_days} days of the "
                f"shipment date: the port-call feed lists the voyage only at other ports/dates ({seen}). "
                "The cargo may not exist.")
    else:
        desc = ("No port call recorded for this vessel and voyage in the port-call feed "
                f"(query scope: {scope}). The cargo may not exist.")
    cites = [Citation(CitationKind.RULE, "R_NO_VESSEL_CALL", snippet=desc)]
    for name in ("vessel", "voyage", "port_of_loading", "shipment_date"):
        c = fields.sources.get(name)
        if c is not None:
            cites.append(c)
    if calls:
        for c in calls:
            cites.append(Citation(CitationKind.REGISTRY, f"vessel_calls:{c['vessel']}/{c['voyage']}/{c['port']}",
                                  snippet=f"{c['vessel']} {c['voyage']} at {c['port']}, arrived {c['arrived']}, "
                                          f"departed {c['departed']}"))
    else:
        cites.append(Citation(CitationKind.REGISTRY, f"vessel_calls:{fields.vessel}/{fields.voyage}",
                              snippet=f"0 rows for query scope: {scope}"))
    return Evidence("R_NO_VESSEL_CALL", desc, RULE_WEIGHTS["R_NO_VESSEL_CALL"], _dedupe_cites(cites))


# ---- cross-document consistency
@dataclass
class _DocLine:
    doc: Document
    page: int
    line: str
    value: float | str

    @property
    def cit(self) -> Citation:
        return Citation(CitationKind.DOCUMENT, self.doc.doc_id, page=self.page, snippet=self.line)


def _doc_quantity(doc: Document) -> _DocLine | None:
    h = _intake._find(doc, _intake._LABELS[doc.doc_type]["quantity"])
    if not h:
        return None
    m = _intake._QTY_RE.search(h.value)
    if not m:
        return None
    try:
        q, u = _intake.normalize_quantity(_intake._parse_num(m.group("num")), m.group("unit"))
    except ValueError:
        return None
    return _DocLine(doc, h.page, h.line, q) if u == "MT" else None


def _doc_commodity(doc: Document) -> _DocLine | None:
    from suraksha.agents.fingerprint import canonical_commodity
    h = _intake._find(doc, _intake._LABELS[doc.doc_type]["commodity"])
    if not h:
        return None
    canon = canonical_commodity(_intake._clip_value(h.value))
    return _DocLine(doc, h.page, h.line, canon) if canon else None


def _doc_money(doc: Document) -> tuple[_DocLine, str] | None:
    h = _intake._find(doc, _intake._LABELS[doc.doc_type]["value"])
    mv = _intake._parse_money(h.value) if h else None
    return (_DocLine(doc, h.page, h.line, mv[0]), mv[1]) if h and mv else None


def _first(req: FinancingRequest, dt: DocType) -> Document | None:
    return next((d for d in req.documents if d.doc_type == dt), None)


def doc_mismatch_check(req: FinancingRequest, settings: Settings) -> Evidence | None:
    """R_DOC_MISMATCH: quantity (> qty_tolerance after unit normalisation) between B/L, invoice and warehouse
    receipt (this includes WR > B/L); commodity (different canonical code) across B/L/invoice/LC/WR; invoice
    value above the LC amount by more than value_tolerance (same currency). Cites both conflicting lines."""
    bl, inv = _first(req, DocType.BILL_OF_LADING), _first(req, DocType.INVOICE)
    lc, wr = _first(req, DocType.LC), _first(req, DocType.WAREHOUSE_RECEIPT)
    problems: list[str] = []
    cites: list[Citation] = []

    def both(a: _DocLine, b: _DocLine) -> None:
        cites.extend([a.cit, b.cit])

    # quantity
    order = [DocType.BILL_OF_LADING, DocType.INVOICE, DocType.WAREHOUSE_RECEIPT]
    docs = {DocType.BILL_OF_LADING: bl, DocType.INVOICE: inv, DocType.WAREHOUSE_RECEIPT: wr}
    qs = {dt: _doc_quantity(d) for dt, d in docs.items() if d is not None}
    qs = {k: v for k, v in qs.items() if v is not None}
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if a in qs and b in qs:
                x, y = qs[a], qs[b]
                big = max(x.value, y.value)
                if big > 0 and abs(x.value - y.value) / big > settings.qty_tolerance:
                    extra = " (warehouse receipt exceeds the B/L quantity)" if (
                        b == DocType.WAREHOUSE_RECEIPT and a == DocType.BILL_OF_LADING and y.value > x.value) else ""
                    problems.append(f"quantity differs by more than {settings.qty_tolerance:.0%}: "
                                    f"{a.value} {x.value:,g} MT vs {b.value} {y.value:,g} MT{extra}")
                    both(x, y)
    # commodity
    cs = [(dt, _doc_commodity(d)) for dt, d in ((DocType.BILL_OF_LADING, bl), (DocType.INVOICE, inv),
                                                  (DocType.LC, lc), (DocType.WAREHOUSE_RECEIPT, wr)) if d is not None]
    cs = [(dt, c) for dt, c in cs if c is not None]
    for i, (a, x) in enumerate(cs):
        for b, y in cs[i + 1:]:
            if x.value != y.value:
                problems.append(f"commodity differs: {a.value} says {x.value}, {b.value} says {y.value}")
                both(x, y)
    # value: invoice vs LC
    if inv is not None and lc is not None:
        vi, vl = _doc_money(inv), _doc_money(lc)
        if vi and vl and vi[1] == vl[1] and vl[0].value > 0 and \
                vi[0].value > vl[0].value * (1 + settings.value_tolerance):
            problems.append(f"invoice value {vi[1]} {vi[0].value:,g} exceeds the LC amount "
                            f"{vl[1]} {vl[0].value:,g} by more than {settings.value_tolerance:.0%}")
            both(vi[0], vl[0])
    if not problems:
        return None
    desc = "Documents are inconsistent with each other: " + "; ".join(problems) + "."
    return Evidence("R_DOC_MISMATCH", desc, RULE_WEIGHTS["R_DOC_MISMATCH"],
                    _dedupe_cites([Citation(CitationKind.RULE, "R_DOC_MISMATCH", snippet=desc)] + cites))


def document_checks(req: FinancingRequest, fields: ExtractedFields, store: Store, settings: Settings) -> list[Evidence]:
    """Evidence from the physical-cargo and cross-document rules (empty = nothing fired)."""
    out = [ev for ev in (vessel_call_check(fields, store, settings), doc_mismatch_check(req, settings))
           if ev is not None]
    if out:
        log.info("document_checks_fired", extra={"ctx": {"request_id": req.request_id,
                                                         "rules": [e.rule_id for e in out]}})
    return out


def document_gaps(req: FinancingRequest, fields: ExtractedFields, store: Store) -> list[str]:
    """Analyst asks for the checks that could NOT run (missing data -> no rule fires, ask instead)."""
    gaps = []
    absent = [n for n, v in (("vessel", fields.vessel), ("voyage", fields.voyage),
                             ("port of loading", fields.port_of_loading),
                             ("shipment date", fields.shipment_date)) if not v]
    if absent:
        gaps.append("Physical-cargo check not run: " + ", ".join(absent) + " not extracted; obtain the original "
                    "B/L and verify the vessel call with the port authority / AIS provider.")
    else:
        has_feed = getattr(store, "has_vessel_call_feed", None)
        in_feed = getattr(store, "vessel_in_feed", None)
        if has_feed is not None and not has_feed():
            gaps.append("Physical-cargo check not run: no port-call (AIS) data is loaded; verify the vessel call "
                        "with the port authority.")
        elif in_feed is not None and not in_feed(fields.vessel):
            gaps.append(f"Physical-cargo check inconclusive: vessel {fields.vessel!r} is not covered by the port-call "
                        "feed; verify the voyage with the carrier or an AIS provider.")
    present = sum(1 for dt in (DocType.BILL_OF_LADING, DocType.INVOICE, DocType.LC, DocType.WAREHOUSE_RECEIPT)
                  if _first(req, dt) is not None)
    if present < 2:
        gaps.append("Cross-document check not run: fewer than two of B/L, invoice, LC and warehouse receipt "
                    "were provided; request the missing documents.")
    return gaps
