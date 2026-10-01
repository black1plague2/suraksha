"""Linkage Q&A over the registry graph (rule-based NL, no LLM)."""
from __future__ import annotations

import re
from typing import Any

from suraksha.agents.investigator import Graph, build_graph, describe_edge, shortest_paths
from suraksha.log import get_logger
from suraksha.models import Citation, CitationKind, GraphEdge
from suraksha.store.base import Store

log = get_logger(__name__)

_SUFFIX = re.compile(r"\b(pvt|private|ltd|limited|llp|fze|fzco|llc|inc|co|corp|corporation)\b\.?", re.I)


def _cname(store: Store, company_id: str) -> str:
    return (store.get_company(company_id) or {}).get("name", company_id)


def linked_entities(company_id: str, store: Store, hops: int = 2) -> list[dict]:
    """Companies reachable within `hops` edges; `via` = shortest connecting edge list."""
    g = build_graph(store)
    out = []
    start = f"company:{company_id}"
    for c in store.list_companies():
        cid = c["company_id"]
        if cid == company_id:
            continue
        ps = shortest_paths(g, start, f"company:{cid}", hops, limit=1)
        if ps:
            out.append({"company_id": cid, "name": c.get("name", cid), "via": ps[0]})
    out.sort(key=lambda r: (len(r["via"]), r["company_id"]))
    log.info("linked_entities", extra={"ctx": {"company_id": company_id, "hops": hops, "found": len(out)}})
    return out


def find_companies(question: str, store: Store) -> list[dict[str, Any]]:
    """Companies mentioned in the question, in order of appearance (id match or name substring)."""
    q = question.lower()
    hits: list[tuple[int, int, dict]] = []  # (pos, length, company)
    taken: list[tuple[int, int]] = []
    comps = store.list_companies()

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    def try_add(c: dict, pat: str) -> bool:
        for m in re.finditer(r"(?<![\w])" + re.escape(pat) + r"(?![\w])", q):
            if free(m.start(), m.end()):
                taken.append((m.start(), m.end()))
                hits.append((m.start(), m.end() - m.start(), c))
                return True
        return False

    done: set[str] = set()
    for c in comps:  # exact ids
        if try_add(c, c["company_id"].lower()):
            done.add(c["company_id"])
    for c in sorted(comps, key=lambda c: -len(c.get("name", ""))):  # full names, longest first
        if c["company_id"] not in done and c.get("name") and try_add(c, c["name"].lower()):
            done.add(c["company_id"])
    for c in sorted(comps, key=lambda c: -len(c.get("name", ""))):  # name without legal suffix
        if c["company_id"] in done or not c.get("name"):
            continue
        core = re.sub(r"\s+", " ", _SUFFIX.sub("", c["name"])).strip(" .,").lower()
        if len(core) >= 4 and try_add(c, core):
            done.add(c["company_id"])
    hits.sort(key=lambda h: h[0])
    return [h[2] for h in hits]


def _fmt_path(g_edges: list[GraphEdge], store: Store) -> str:
    return "; ".join(describe_edge(e) for e in g_edges)


def answer(question: str, store: Store) -> dict:
    q = question.lower()
    comps = find_companies(question, store)

    if not comps:
        res = {"intent": "unknown", "rows": [], "citations": [],
               "answer_text": "I could not find a company in your question. Mention a company id (e.g. C0001) or "
                              "name, e.g. 'Who is linked to <company>?', 'Path between <A> and <B>', or "
                              "'Directors of <company>'."}
        log.info("linkage_answer", extra={"ctx": {"intent": "unknown", "question": question}})
        return res

    if re.search(r"\b(director|directors|owner|owners|ubo|ubos|shareholder|shareholders|owns|ownership)\b", q):
        c = comps[0]
        cid = c["company_id"]
        rows, cites = [], []
        for r in store.roles_for_company(cid):
            p = store.get_person(r["person_id"]) or {}
            rows.append({"company_id": cid, "person_id": r["person_id"], "name": p.get("name", r["person_id"]),
                         "role": r["role"], "pct": r.get("pct")})
            cites.append(Citation(CitationKind.REGISTRY, f"roles:{cid}/{r['person_id']}/{r['role']}",
                                  snippet=f"{p.get('name', r['person_id'])} is {r['role']}"))
        for o in store.corp_owners_of(cid):
            rows.append({"company_id": cid, "owner_company_id": o["owner_company_id"],
                         "name": _cname(store, o["owner_company_id"]), "role": "CORPORATE_OWNER",
                         "pct": o.get("pct")})
            cites.append(Citation(CitationKind.REGISTRY, f"corp_owners:{o['owner_company_id']}/{cid}",
                                  snippet=f"{_cname(store, o['owner_company_id'])} owns {o.get('pct')}%"))
        if rows:
            parts = [f"{r['name']} ({r['role']}" + ("" if r.get("pct") is None else f", {r['pct']}%") + ")"
                     for r in rows]
            text = f"{c['name']} ({cid}) has: " + ", ".join(parts) + "."
        else:
            text = f"No directors, owners or shareholders are recorded for {c['name']} ({cid})."
        intent = "roles"

    elif len(comps) >= 2 and re.search(r"\b(path|between|connection|link|linked|related|connected|relationship)\b", q):
        a, b = comps[0], comps[1]
        from suraksha.config import get_settings
        g = build_graph(store)
        ps = shortest_paths(g, f"company:{a['company_id']}", f"company:{b['company_id']}",
                            get_settings().max_graph_hops)
        rows = [{"from": a["company_id"], "to": b["company_id"],
                 "edges": [describe_edge(e) for e in p]} for p in ps]
        cites = [e.citation for p in ps for e in p]
        if ps:
            text = (f"{a['name']} ({a['company_id']}) and {b['name']} ({b['company_id']}) are connected in "
                    f"{len(ps[0])} hop(s): {_fmt_path(ps[0], store)}.")
        else:
            text = f"No link found between {a['name']} and {b['name']} within the search depth."
        intent = "path"

    else:
        c = comps[0]
        ents = linked_entities(c["company_id"], store)
        rows = [{"company_id": e["company_id"], "name": e["name"], "hops": len(e["via"]),
                 "via": [describe_edge(x) for x in e["via"]]} for e in ents]
        cites = [x.citation for e in ents for x in e["via"]]
        if ents:
            text = (f"{len(ents)} entit{'y is' if len(ents) == 1 else 'ies are'} linked to {c['name']} "
                    f"({c['company_id']}): " + "; ".join(f"{e['name']} ({e['company_id']}, {len(e['via'])} hops)"
                                                          for e in ents) + ".")
        else:
            text = f"No other companies are linked to {c['name']} ({c['company_id']}) in the registry."
        intent = "linked_entities"

    seen, uniq = set(), []
    for ct in cites:
        if (ct.kind, ct.ref) not in seen:
            seen.add((ct.kind, ct.ref))
            uniq.append(ct)
    log.info("linkage_answer", extra={"ctx": {"intent": intent, "question": question, "rows": len(rows)}})
    return {"intent": intent, "answer_text": text, "rows": rows, "citations": uniq}
