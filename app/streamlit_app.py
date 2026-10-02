"""Suraksha - duplicate-financing detector. One Streamlit app, four personas.

Run locally:   streamlit run app/streamlit_app.py
Backend (auto-detected, see `detect_backend`):
  1. inside Streamlit-in-Snowflake (an active Snowpark session exists) -> "snowflake": read-only
     dashboard over the rows CALL LOAD_SYNTH / CALL RUN_PIPELINE wrote; decisions go through
     CALL SURAKSHA.CORE.DECIDE_CASE; the pipeline is NOT re-run.
  2. SURAKSHA_BACKEND=snowflake -> same, over a snowflake.connector connection from env.
  3. otherwise SURAKSHA_BACKEND (default "memory": synthetic data generated in-process).
Imports are limited to streamlit + pandas + stdlib + suraksha (snowflake.* only lazily, in Snowflake mode).
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# Allow `streamlit run app/streamlit_app.py` from a source checkout without installing.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from suraksha.agents import linkage  # noqa: E402
from suraksha.agents.approval import AuditLog  # noqa: E402
from suraksha.agents.investigator import describe_edge  # noqa: E402
from suraksha.agents.report import render_markdown  # noqa: E402
from suraksha.config import RULE_WEIGHTS, get_settings  # noqa: E402
from suraksha.models import CaseStatus, Decision, PipelineStatus  # noqa: E402
from suraksha.pipeline import Suraksha  # noqa: E402

try:  # jurisdiction STR templates (FIU-IND / UAE-goAML); optional - fall back to the generic markdown
    from suraksha.agents import report_templates  # noqa: E402
except Exception:  # not present yet / import problem
    report_templates = None

PAGES = ["Trade-ops analyst", "Investigator", "Compliance officer (MLRO)", "Risk head", "Policy what-if"]
STATUS_LABEL = {
    PipelineStatus.CLEAR: "CLEAR",
    PipelineStatus.NEED_MORE_EVIDENCE: "HOLD-for-evidence",
    PipelineStatus.PENDING_APPROVAL: "PENDING_APPROVAL",
}
BADGE = {"CLEAR": "🟢", "HOLD-for-evidence": "🟡", "PENDING_APPROVAL": "🔴", "FILED": "🟣", "CLOSED": "⚪"}


# --------------------------------------------------------------------------- data
@st.cache_resource(show_spinner="Generating synthetic consortium and screening requests...")
def load_app():
    """Build the store + pipeline and process every synthetic request once."""
    from suraksha.store.memory import MemoryStore
    from suraksha.synth import generate, load_into

    ds = generate(seed=42)
    store = MemoryStore()
    load_into(store, ds)
    engine = Suraksha(store)
    results = {r.request_id: engine.process(r) for r in ds.requests}
    return {"mode": "memory", "store": store, "engine": engine, "requests": ds.requests, "results": results,
            "labels": dict(ds.labels)}


# ------------------------------------------------------------- backend detection (Snowflake mode)
class _Cur:
    """DB-API cursor shim over a Snowpark-hosted connector cursor (same approach as sql/08)."""

    def __init__(self, cur):
        self._c = cur

    def execute(self, sql, params=None):
        from suraksha.store.snowflake import inline_nulls  # None would bind as the string 'None'
        sql, params = inline_nulls(sql.replace("%s", "?"), params)  # Snowpark-hosted connector: qmark
        return self._c.execute(sql, params) if params else self._c.execute(sql)

    def executemany(self, sql, rows):
        from suraksha.store.snowflake import batch_values_insert  # multi-row INSERTs (per-row was too slow live)
        rows = [tuple(r) for r in rows]
        stmts = batch_values_insert(sql.replace("%s", "?"), rows)
        if stmts is None:
            for r in rows:
                self.execute(sql, r)
            return
        for s, p in stmts:
            self._c.execute(s, p) if p else self._c.execute(s)

    def fetchall(self):
        return self._c.fetchall()

    @property
    def description(self):
        return self._c.description

    def close(self):
        self._c.close()


class _Conn:
    """Minimal DB-API connection over a Snowpark session (what SnowflakeStore expects)."""
    paramstyle = "qmark"  # Snowpark session connections bind with ? (live ITER-04)

    def __init__(self, session):
        raw = getattr(session, "connection", None)
        if raw is None:
            raw = session._conn._conn
        self._raw = raw

    def cursor(self):
        return _Cur(self._raw.cursor())

    def close(self):
        pass


def detect_backend() -> tuple[str, object | None]:
    """('snowflake', session|None) | ('memory', None).

    Active Snowpark session (Streamlit-in-Snowflake) wins; else SURAKSHA_BACKEND; default memory.
    """
    try:
        from snowflake.snowpark.context import get_active_session

        return "snowflake", get_active_session()
    except Exception:  # not inside Snowflake / snowpark not installed
        pass
    if os.getenv("SURAKSHA_BACKEND", "memory").strip().lower() == "snowflake":
        return "snowflake", None  # connect from env vars
    return "memory", None


@st.cache_resource(show_spinner="Connecting to Snowflake...")
def load_snowflake_store(_session):
    from suraksha.store.snowflake import SnowflakeStore

    from suraksha.store.cached import RegistryCachedStore  # graph walks hit the registry thousands of times
    return RegistryCachedStore(SnowflakeStore(_Conn(_session)) if _session is not None else SnowflakeStore())


def snowflake_rows(store) -> list[dict]:
    """One dict per CORE.PIPELINE_RESULTS row, joined with the request, extracted commodity and case status."""
    banks = " UNION ALL ".join(
        f"SELECT request_id, bank_id, borrower_id, amount, currency, submitted_at FROM SURAKSHA.{b}.REQUESTS"
        for b in ("BANK_A", "BANK_B", "BANK_C"))
    recs = store._query(
        "SELECT p.request_id, p.status, p.py_score, p.py_band, p.py_rules, p.label_duplicate, r.bank_id, r.borrower_id, r.amount, "
        f"r.currency, r.submitted_at, f.commodity FROM SURAKSHA.CORE.PIPELINE_RESULTS p LEFT JOIN ({banks}) r "
        "ON r.request_id = p.request_id LEFT JOIN SURAKSHA.CORE.REQUEST_FIELDS f ON f.request_id = p.request_id "
        "ORDER BY r.submitted_at, p.request_id")
    cases = {c.request_id: c for c in store.list_cases()}
    out = []
    for r in recs:
        rules = r.get("py_rules")
        if isinstance(rules, (str, bytes)):
            rules = json.loads(rules)
        pstatus = PipelineStatus(r["status"]) if r.get("status") else PipelineStatus.CLEAR
        label = STATUS_LABEL[pstatus]
        case = cases.get(r["request_id"])
        if case is not None and case.status is not CaseStatus.PENDING_APPROVAL:
            label = case.status.value
        out.append({**r, "py_rules": list(rules or []), "pipeline_status": pstatus, "label": label,
                    "case": case})
    return out


def audit_verify_snowflake(store) -> tuple[bool, int, int | None]:
    """(python_chain_ok, n_records, sql_breaks). Python recompute is the same AuditLog.verify logic."""
    class _Norm:  # Snowflake returns naive UTC timestamps; the hash was computed over aware UTC
        def __init__(self, recs):
            self._recs = recs

        def list_audit(self):
            return self._recs

    recs = store.list_audit()
    for r in recs:
        if r.at.tzinfo is None:
            r.at = r.at.replace(tzinfo=timezone.utc)
    ok = AuditLog(_Norm(recs)).verify()
    breaks = None
    try:
        row = store._one("SELECT COUNT_IF(chain_status <> 'OK') AS breaks FROM SURAKSHA.CORE.V_AUDIT_VERIFY")
        breaks = int(row["breaks"]) if row else None
    except Exception:
        breaks = None
    return ok, len(recs), breaks


def decide_snowflake(store, case_id: str, decision: Decision, officer: str, reason: str) -> dict:
    """CALL SURAKSHA.CORE.DECIDE_CASE; raises with the server's message if refused."""
    row = store._query("CALL SURAKSHA.CORE.DECIDE_CASE(%s, %s, %s, %s)",
                       (case_id, decision.value, officer, reason or ""))
    val = list(row[0].values())[0] if row else None
    if isinstance(val, (str, bytes)):
        try:
            val = json.loads(val)
        except ValueError:
            return {"message": str(val)}
    if isinstance(val, dict):
        err = val.get("error") or val.get("ERROR")
        if err:
            raise RuntimeError(str(err))
        return val
    return {"message": str(val)}


def effective_status(app, rid: str) -> str:
    if app["mode"] == "snowflake":
        return next(r["label"] for r in app["rows"] if r["request_id"] == rid)
    res = app["results"][rid]
    if res.case is not None:
        case = app["store"].get_case(res.case.case_id)
        if case is not None and case.status is not CaseStatus.PENDING_APPROVAL:
            return case.status.value
    return STATUS_LABEL[res.status]


def company_name(store, cid: str) -> str:
    return (store.get_company(cid) or {}).get("name", cid)


def plain_reason(app, rid: str) -> str:
    if app["mode"] == "snowflake":
        r = next(x for x in app["rows"] if x["request_id"] == rid)
        if r["pipeline_status"] is PipelineStatus.CLEAR:
            return "No matching pledge found at other consortium banks."
        score = f" Confidence {r['py_score']:.2f} ({r['py_band']})." if r.get("py_score") is not None else ""
        return "Flagged by the pipeline: " + (", ".join(r["py_rules"]) or "no rules recorded") + "." + score
    res = app["results"][rid]
    if res.status is PipelineStatus.CLEAR:
        return "No matching pledge found at other consortium banks."
    inv, conf = res.investigation, res.confidence
    bits = []
    if inv is not None:
        m = inv.match
        when = f" {inv.timing_overlap_days} days earlier" if inv.timing_overlap_days is not None else ""
        kind = "Same" if m.match_type.value == "EXACT" else "Near-identical"
        bits.append(f"{kind} cargo already pledged at {m.entry.bank_id}{when}.")
    if conf is not None:
        links = [e.description for e in conf.evidence if e.rule_id not in ("R_EXACT_HASH", "R_FUZZY_MATCH")]
        if links:
            bits.append("; ".join(d.rstrip(".") for d in links) + ".")
        bits.append(f"Confidence {conf.score:.2f} ({conf.band.value}).")
    return " ".join(bits)


def requests_df(app) -> pd.DataFrame:
    rows = []
    if app["mode"] == "snowflake":
        names = {c["company_id"]: c["name"] for c in app["store"].list_companies()}
        for r in app["rows"]:
            sub = r.get("submitted_at")
            rows.append({
                "request_id": r["request_id"], "bank": r.get("bank_id") or "?",
                "borrower": names.get(r.get("borrower_id"), r.get("borrower_id") or "?"),
                "commodity": r.get("commodity") or "unknown",
                "amount": float(r["amount"] or 0), "currency": r.get("currency") or "",
                "submitted": sub.strftime("%Y-%m-%d") if sub is not None else "",
                "status": r["label"], "badge": f"{BADGE.get(r['label'], '')} {r['label']}",
            })
        return pd.DataFrame(rows)
    for req in app["requests"]:
        res = app["results"][req.request_id]
        status = effective_status(app, req.request_id)
        rows.append({
            "request_id": req.request_id,
            "bank": req.bank_id,
            "borrower": company_name(app["store"], req.borrower_id),
            "commodity": (res.fields.commodity if res.fields else None) or "unknown",
            "amount": req.amount,
            "currency": req.currency,
            "submitted": req.submitted_at.strftime("%Y-%m-%d"),
            "status": status,
            "badge": f"{BADGE.get(status, '')} {status}",
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- shared UI
def citation_rows(cits) -> pd.DataFrame:
    return pd.DataFrame([{"kind": c.kind.value, "ref": c.ref, "page": c.page, "snippet": c.snippet} for c in cits])


def md_for_streamlit(md: str) -> str:
    """st.markdown has no footnotes; turn [^n] markers into bold [n].
    Trailing space keeps adjacent markers from fusing into '****'."""
    md = re.sub(r"^\[\^(\d+)\]:", r"**[\1]**", md, flags=re.M)
    return re.sub(r"\[\^(\d+)\]", r"**[\1]** ", md)


def fields_table(f) -> pd.DataFrame:
    """ExtractedFields -> field / value / source / snippet rows (only populated fields)."""
    out = []
    for name in ("bl_number", "vessel", "voyage", "port_of_loading", "port_of_discharge", "commodity",
                 "quantity", "quantity_unit", "value", "currency", "shipment_date", "shipper", "consignee"):
        val = getattr(f, name)
        if val is None:
            continue
        c = f.sources.get(name)
        src = ""
        if c is not None:
            src = f"{c.kind.value}: {c.ref}" + (f" p.{c.page}" if c.page else "")
        out.append({"field": name, "value": str(val), "source": src, "snippet": (c.snippet if c else "") or ""})
    return pd.DataFrame(out, columns=["field", "value", "source", "snippet"])


def draw_path(store, path) -> None:
    """Ownership path as node -> (relation) -> node graphviz diagram."""
    def label(node: str) -> str:
        kind, _, ident = node.partition(":")
        if kind == "company":
            return f"{(store.get_company(ident) or {}).get('name', ident)}\\n{ident}"
        return node

    dot = ['digraph G { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#EEF2FF", '
           'color="#4F46E5", fontname="Helvetica", fontsize=11]; edge [color="#64748B", fontname="Helvetica", '
           'fontsize=9];']
    ids: dict[str, str] = {}
    for e in path.edges:
        for n in (e.src, e.dst):
            if n not in ids:
                ids[n] = f"n{len(ids)}"
                dot.append(f'{ids[n]} [label="{label(n).replace(chr(34), chr(39))}"];')
        dot.append(f'{ids[e.src]} -> {ids[e.dst]} [label="{e.relation}"];')
    dot.append("}")
    st.graphviz_chart(chr(10).join(dot))


def draw_chain(edges_text: list[str]) -> None:
    """Render describe_edge() strings as a left-to-right node chain (graphviz)."""
    if not edges_text:
        return
    dot = ['digraph G { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#EEF2FF", '
           'color="#4F46E5", fontname="Helvetica", fontsize=11]; edge [color="#64748B"];']
    for i, t in enumerate(edges_text):
        t = t.replace('"', "'")
        dot.append(f'n{i} [label="{t}"];')
        if i:
            dot.append(f"n{i-1} -> n{i};")
    dot.append("}")
    st.graphviz_chart("\n".join(dot))


CSS = """
<style>
.block-container {padding-top: 1.6rem;}
.sk-banner {background:#FFF7E6;border:1px solid #F5C469;color:#7A4B00;padding:.45rem .8rem;
  border-radius:8px;font-size:.85rem;margin-bottom:1rem;}
.sk-title {font-size:1.6rem;font-weight:700;margin:0;}
.sk-sub {color:#64748B;margin:0 0 1rem 0;}
</style>
"""


def header(title: str, sub: str) -> None:
    st.markdown(f'<p class="sk-title">{title}</p><p class="sk-sub">{sub}</p>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- pages
def page_analyst(app) -> None:
    header("Intake queue", "Every new request is checked against cargo already pledged at other banks.")
    df = requests_df(app)
    c1, c2 = st.columns(2)
    bank = c1.selectbox("Bank", ["All"] + sorted(df["bank"].unique()), key="analyst_bank")
    stat = c2.multiselect("Status", sorted(df["status"].unique()), default=[], key="analyst_status")
    view = df
    if bank != "All":
        view = view[view["bank"] == bank]
    if stat:
        view = view[view["status"].isin(stat)]
    st.dataframe(view[["request_id", "bank", "borrower", "commodity", "amount", "currency", "submitted", "badge"]]
                 .rename(columns={"badge": "status"}), use_container_width=True, hide_index=True)
    if view.empty:
        st.info("No requests match the filter.")
        return
    rid = st.selectbox("Open request", list(view["request_id"]), key="analyst_pick")
    row = df[df["request_id"] == rid].iloc[0]
    st.subheader(f"{rid}  ·  {row['badge']}")
    st.info(plain_reason(app, rid))
    if app["mode"] == "snowflake":
        res = None
        f = app["store"].get_fields(rid)
    else:
        res = app["results"][rid]
        f = res.fields
    if f is None:
        return
    st.markdown("**Extracted fields (with source)**")
    st.dataframe(fields_table(f), use_container_width=True, hide_index=True)
    if res is None:
        r = next(x for x in app["rows"] if x["request_id"] == rid)
        if r["py_rules"]:
            st.markdown("**Evidence (rules fired)**")
            st.dataframe(pd.DataFrame({"rule": r["py_rules"]}), use_container_width=True, hide_index=True)
        return
    if res.confidence and res.confidence.evidence:
        st.markdown("**Evidence**")
        st.dataframe(pd.DataFrame([{"rule": e.rule_id, "weight": e.weight, "finding": e.description}
                                   for e in res.confidence.evidence]), use_container_width=True, hide_index=True)
    if res.confidence and res.confidence.missing:
        st.warning("Evidence needed before this can proceed:\n\n" + "\n".join(f"- {m}" for m in res.confidence.missing))


def page_investigator(app) -> None:
    store = app["store"]
    header("Investigator", "Ask a plain-English question about companies, owners and links.")
    comps = store.list_companies()
    if len(comps) >= 2:
        a, b = comps[0]["name"], comps[1]["name"]
        st.caption(f"Try: `Who else is linked to {a}?` · `Directors of {a}` · `Path between {a} and {b}`")
    q = st.text_input("Question", key="inv_q", placeholder="who else is linked to <company>?")
    if q.strip():
        try:
            ans = linkage.answer(q, store)
        except Exception as exc:  # show, never crash
            st.error(f"Could not answer: {exc}")
        else:
            st.success(ans["answer_text"])
            if ans["intent"] == "path":
                for r in ans["rows"][:3]:
                    st.markdown(f"**Ownership path {r['from']} → {r['to']}**")
                    draw_chain(r["edges"])
            elif ans["intent"] == "linked_entities" and ans["rows"]:
                sel = st.selectbox("Show path to", [f"{r['company_id']} {r['name']}" for r in ans["rows"]],
                                   key="inv_sel")
                r = next(r for r in ans["rows"] if sel.startswith(r["company_id"]))
                draw_chain(r["via"])
            if ans["rows"]:
                flat = [{k: (" | ".join(v) if isinstance(v, list) else v) for k, v in r.items()} for r in ans["rows"]]
                st.markdown("**Rows**")
                st.dataframe(pd.DataFrame(flat), use_container_width=True, hide_index=True)
            if ans["citations"]:
                with st.expander(f"Citations ({len(ans['citations'])})"):
                    st.dataframe(citation_rows(ans["citations"]), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Cases needing more evidence")
    if app["mode"] == "snowflake":
        weak_rows = [r for r in app["rows"] if r["pipeline_status"] is PipelineStatus.NEED_MORE_EVIDENCE]
        if not weak_rows:
            st.caption("None.")
        for r in weak_rows:
            score = f"{r['py_score']:.2f}" if r.get("py_score") is not None else "-"
            with st.expander(f"{r['request_id']} · score {score} ({r.get('py_band')})"):
                st.write(plain_reason(app, r["request_id"]))
        return
    weak = [(rid, r) for rid, r in app["results"].items() if r.status is PipelineStatus.NEED_MORE_EVIDENCE]
    if not weak:
        st.caption("None.")
    for rid, r in weak:
        conf, inv = r.confidence, r.investigation
        bank = f"{inv.match.entry.bank_id} match" if inv is not None else "document check"
        score = f"{conf.score:.2f}" if conf is not None else "-"
        with st.expander(f"{rid} · {bank} · score {score} (LOW)"):
            st.write(plain_reason(app, rid))
            st.markdown("**Missing evidence:**")
            for m in (conf.missing if conf is not None else []) or ["Analyst review of the flagged documents."]:
                st.markdown(f"- {m}")
            for p in (inv.paths[:1] if inv is not None else []):
                if p.edges:
                    draw_chain([describe_edge(e) for e in p.edges])


# ---------------------------------------------------------------------- evidence chain (glass box)
def str_markdown(draft, jurisdiction: str | None = None) -> str:
    """STR text; jurisdiction template when the module exists, generic markdown otherwise."""
    if report_templates is not None and jurisdiction:
        try:
            return report_templates.render(draft, jurisdiction)
        except Exception as exc:  # never break the case page over a template
            st.warning(f"Template '{jurisdiction}' failed ({exc}); showing the generic report.")
    return render_markdown(draft)


def snowflake_facts(store, rid: str) -> dict | None:
    try:
        return store._one("SELECT * FROM SURAKSHA.CORE.INVESTIGATION_FACTS WHERE request_id = %s", (rid,))
    except Exception:
        return None


def rule_rows(app, rid: str) -> tuple[list[dict], float | None, str | None]:
    """[{rule, weight, finding}], score, band for one request (memory: evidence; snowflake: py_rules)."""
    if app["mode"] == "snowflake":
        r = next(x for x in app["rows"] if x["request_id"] == rid)
        rows = [{"rule": k, "weight": RULE_WEIGHTS.get(k), "finding": "(fired - see V_RULE_EVIDENCE)"}
                for k in r["py_rules"]]
        return rows, r.get("py_score"), r.get("py_band")
    conf = app["results"][rid].confidence
    if conf is None:
        return [], None, None
    return ([{"rule": e.rule_id, "weight": e.weight, "finding": e.description} for e in conf.evidence],
            conf.score, conf.band.value)


def render_evidence_chain(app, case, draft) -> None:
    """One ordered chain: Documents -> Consortium match -> Ownership path -> Rules -> STR."""
    store, rid = app["store"], case.request_id
    res = app["results"].get(rid) if app["mode"] == "memory" else None
    inv = res.investigation if res else None
    st.markdown("#### Evidence chain")
    st.caption("Every step below is derived from stored data - nothing is generated at review time.")

    with st.expander("1 · Documents - extracted fields and source lines", expanded=False):
        f = res.fields if res else store.get_fields(rid)
        if f is None:
            st.caption("No extracted fields stored for this request.")
        else:
            st.dataframe(fields_table(f), use_container_width=True, hide_index=True)

    with st.expander("2 · Consortium match - hashed fingerprints only", expanded=True):
        if inv is not None:
            m = inv.match
            days = inv.timing_overlap_days
            c = st.columns(4)
            c[0].metric("Match type", m.match_type.value)
            c[1].metric("Other bank", m.entry.bank_id)
            c[2].metric("Days apart", "-" if days is None else days)
            c[3].metric("Similarity", f"{m.similarity:.2f}")
            st.dataframe(pd.DataFrame([{"fingerprint key": k, "matched hash (prefix)": m.entry.keys.get(k, "")[:8] + "…"}
                                       for k in m.matched_keys]), use_container_width=True, hide_index=True)
            st.caption("Only salted-hash prefixes are shown: no names, no amounts cross the bank boundary.")
        elif app["mode"] == "snowflake":
            facts = snowflake_facts(store, rid)
            if facts:
                st.write(f"Match type **{facts.get('match_type')}** · timing overlap "
                         f"**{facts.get('timing_overlap_days')}** day(s) (CORE.INVESTIGATION_FACTS).")
            else:
                st.caption("Match facts not available (CORE.INVESTIGATION_FACTS unreadable or empty).")
        else:
            st.caption("No consortium match.")

    with st.expander("3 · Ownership path - node → relation → node", expanded=False):
        paths = [p for p in (inv.paths if inv else []) if p.edges]
        if paths:
            draw_path(store, paths[0])
            st.caption(" → ".join(describe_edge(e) for e in paths[0].edges))
        elif inv is not None and inv.counterparty_company_id == inv.borrower_id:
            st.write("Same legal entity at both banks (identical borrower token) - no path needed.")
        elif app["mode"] == "snowflake":
            st.caption("Graph walks run in the pipeline; use the Investigator page for live path queries.")
        else:
            st.caption("No ownership link found between the two borrowers.")

    with st.expander("4 · Rules fired - weight, sum, threshold", expanded=True):
        rows, score, band = rule_rows(app, rid)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            total = sum(r["weight"] or 0 for r in rows)
            thr = get_settings().confidence_threshold
            st.progress(min(1.0, float(score if score is not None else min(1.0, total))))
            st.write(f"Sum of weights **{total:.2f}** (capped at 1.00) = score **{0 if score is None else score:.2f}** "
                     f"· threshold **{thr:.2f}** · band **{band}**")
        else:
            st.caption("No rules fired.")

    with st.expander("5 · Suspicious Transaction Report (STR)", expanded=False):
        if draft is None:
            st.caption("No STR drafted for this case.")
        else:
            juris = None
            if report_templates is not None:
                opts = ["Generic"] + list(getattr(report_templates, "JURISDICTIONS", ()))
                pick = st.radio("Jurisdiction template", opts, horizontal=True, key=f"juris_{case.case_id}")
                juris = None if pick == "Generic" else pick
            with st.container(border=True):
                st.markdown(md_for_streamlit(str_markdown(draft, juris)))


def page_mlro(app) -> None:
    store, engine = app["store"], app.get("engine")
    header("Compliance officer (MLRO)", "Review the evidence chain and drafted STR, then approve or reject as a named officer.")
    if st.session_state.get("decided_msg"):
        st.success(st.session_state.pop("decided_msg"))
    cases = store.list_cases()
    pending = [c for c in cases if c.status is CaseStatus.PENDING_APPROVAL]
    st.caption(f"{len(pending)} pending · {len(cases) - len(pending)} decided")
    if not cases:
        st.info("No cases yet.")
        return
    only_pending = st.checkbox("Pending only", value=True, key="mlro_pending")
    shown = pending if only_pending else cases
    if not shown:
        st.success("No pending cases.")
        return
    st.dataframe(pd.DataFrame([{"case": c.case_id, "request": c.request_id, "status": c.status.value,
                                "hold": c.hold_recommended, "decided_by": c.decided_by} for c in shown]),
                 use_container_width=True, hide_index=True)
    cid = st.selectbox("Case", [c.case_id for c in shown], key="mlro_case")
    case = store.get_case(cid)
    draft = store.get_report(case.report_id)
    st.subheader(f"{cid} · {case.status.value}")
    render_evidence_chain(app, case, draft)
    st.markdown("#### 6 · Decision & audit trail")
    if case.status is CaseStatus.PENDING_APPROVAL:
        with st.form(f"decide_{cid}"):
            officer = st.text_input("Officer name (required)", key=f"officer_{cid}")
            reason = st.text_area("Reason (required to reject)", key=f"reason_{cid}")
            b1, b2 = st.columns(2)
            approve = b1.form_submit_button("Approve - file case & recommend hold", type="primary")
            reject = b2.form_submit_button("Reject - close case")
        if approve or reject:
            decision = Decision.APPROVE if approve else Decision.REJECT
            try:
                if app["mode"] == "snowflake":
                    out = decide_snowflake(store, cid, decision, officer, reason)
                else:
                    engine.decide(cid, decision, officer, reason)
            except Exception as exc:
                st.error(str(exc))
            else:
                if app["mode"] == "snowflake":
                    st.session_state["decided_msg"] = f"DECIDE_CASE({cid}): {json.dumps(out, default=str)}"
                else:
                    st.session_state["decided_msg"] = (
                        f"Case {cid} {'filed' if approve else 'closed'} by {officer.strip()}.")
                st.rerun()
    else:
        when = f"{case.decided_at:%Y-%m-%d %H:%M} UTC" if case.decided_at else "-"
        st.info(f"Decided by {case.decided_by} at {when}. Reason: {case.reason or '-'}")

    st.subheader("Audit trail")
    recs = store.list_audit(cid) + store.list_audit(case.request_id)
    recs.sort(key=lambda r: r.seq)
    st.dataframe(pd.DataFrame([{"seq": r.seq, "at": r.at.strftime("%Y-%m-%d %H:%M:%S"), "actor": r.actor,
                                "action": r.action, "subject": r.subject_id, "hash": r.entry_hash[:12]}
                               for r in recs]), use_container_width=True, hide_index=True)
    if st.button("Verify audit chain", key="verify"):
        if app["mode"] == "snowflake":
            ok, n, breaks = audit_verify_snowflake(store)
            sql_txt = "unavailable" if breaks is None else f"{breaks} break(s)"
            if ok and not breaks:
                st.success(f"Audit chain intact ({n} records recomputed in Python). "
                           f"CORE.V_AUDIT_VERIFY: {sql_txt}.")
            else:
                st.error(f"Audit chain verification FAILED (python recompute ok={ok}); "
                         f"CORE.V_AUDIT_VERIFY: {sql_txt}.")
        elif engine.audit.verify():
            st.success(f"Audit chain intact ({len(store.list_audit())} records, hashes verified).")
        else:
            st.error("Audit chain verification FAILED - tampering or gap detected.")


def time_to_finding(app) -> pd.Series:
    """Seconds from REQUEST_RECEIVED to CASE_OPENED (drafted STR) per request, from audit timestamps."""
    store = app["store"]
    case_req = {c.case_id: c.request_id for c in store.list_cases()}
    start: dict[str, object] = {}
    end: dict[str, object] = {}
    for r in store.list_audit():
        if r.action == "REQUEST_RECEIVED":
            start[r.subject_id] = r.at
        elif r.action == "CASE_OPENED" and r.subject_id in case_req:
            end[case_req[r.subject_id]] = r.at
    vals = {}
    for rid, t1 in end.items():
        t0 = start.get(rid)
        if t0 is not None:
            vals[rid] = abs((t1.replace(tzinfo=None) - t0.replace(tzinfo=None)).total_seconds())
    return pd.Series(vals, dtype=float)


def page_risk(app) -> None:
    header("Risk head", "Flagged exposure and daily summary across the consortium.")
    df = requests_df(app)
    df["is_flagged"] = df["status"] != "CLEAR"
    df["exposure"] = df["amount"].where(df["is_flagged"], 0.0)
    flagged = df[df["is_flagged"]]
    k = st.columns(4)
    k[0].metric("Requests", len(df))
    k[1].metric("Flagged", len(flagged))
    k[2].metric("Pending approval", int((df["status"] == "PENDING_APPROVAL").sum()))
    k[3].metric("Filed", int((df["status"] == "FILED").sum()))

    st.subheader("Operational metrics")
    held = df[df["status"].isin(["PENDING_APPROVAL", "FILED"])].groupby("currency")["amount"].sum()
    m = st.columns(3)
    with m[0]:
        st.markdown("**Exposure held** (PENDING + FILED)")
        if held.empty:
            st.caption("None.")
        for cur, amt in held.items():
            st.metric(cur or "?", f"{amt:,.0f}")
    try:
        ttf = time_to_finding(app)
    except Exception:
        ttf = pd.Series(dtype=float)
    med = "-" if ttf.empty else (f"{ttf.median() * 1000:,.0f} ms" if ttf.median() < 2 else f"{ttf.median():,.1f} s")
    m[1].metric("Median time: request to drafted finding", med,
                help="Audit-log REQUEST_RECEIVED to CASE_OPENED, per request with a drafted STR.")
    m[2].metric("Analyst queue (need more evidence)", int((df["status"] == "HOLD-for-evidence").sum()))

    st.subheader("Flagged exposure by currency")
    exp = flagged.groupby("currency", as_index=False)["amount"].sum()
    if exp.empty:
        st.caption("No flagged exposure.")
    else:
        for col, (_, r) in zip(st.columns(len(exp)), exp.iterrows()):
            col.metric(r["currency"], f"{r['amount']:,.0f}")
    c1, c2 = st.columns(2)
    for col, key, title in ((c1, "bank", "By bank"), (c2, "commodity", "By commodity")):
        with col:
            st.subheader(title)
            g = df.groupby(key).agg(requests=("request_id", "count"), flagged=("is_flagged", "sum"),
                                    flagged_exposure=("exposure", "sum")).reset_index()
            st.dataframe(g, use_container_width=True, hide_index=True)
            st.bar_chart(g.set_index(key)[["requests", "flagged"]])
    st.subheader("Daily summary")
    daily = df.groupby("submitted").agg(requests=("request_id", "count"), flagged=("is_flagged", "sum"),
                                        flagged_exposure=("exposure", "sum")).reset_index()
    st.dataframe(daily.sort_values("submitted", ascending=False), use_container_width=True, hide_index=True)
    st.caption("Status mix: " + ", ".join(f"{k} {v}" for k, v in Counter(df["status"]).items()))


# ---------------------------------------------------------------------- policy what-if
def whatif_frame(app) -> tuple[pd.DataFrame, list[str], bool]:
    """Request x rule 0/1 matrix + label. Returns (frame, rule_ids, labels_available)."""
    recs = []
    if app["mode"] == "snowflake":
        for r in app["rows"]:
            lab = r.get("label_duplicate")
            recs.append((r["request_id"], list(r.get("py_rules") or []), None if lab is None else bool(lab)))
    else:
        labels = app.get("labels", {})
        for rid, res in app["results"].items():
            fired = [e.rule_id for e in res.confidence.evidence] if res.confidence else []
            recs.append((rid, fired, labels.get(rid)))
    seen = {rule for _, fired, _ in recs for rule in fired}
    rules = list(RULE_WEIGHTS) + sorted(seen - set(RULE_WEIGHTS))
    mat = np.zeros((len(recs), len(rules)))
    idx = {r: i for i, r in enumerate(rules)}
    for i, (_, fired, _) in enumerate(recs):
        for rule in set(fired):
            mat[i, idx[rule]] = 1.0
    frame = pd.DataFrame(mat, columns=rules)
    frame.insert(0, "request_id", [r[0] for r in recs])
    labels_ok = bool(recs) and all(r[2] is not None for r in recs)
    frame["label"] = [bool(r[2]) if r[2] is not None else False for r in recs]
    return frame, rules, labels_ok


def simulate(frame: pd.DataFrame, rules: list[str], weights: dict[str, float], threshold: float) -> dict:
    """Vectorised re-score: score = min(1, sum of fired weights) rounded to 4dp, HIGH if >= threshold."""
    w = np.array([weights.get(r, 0.0) for r in rules], dtype=float)
    score = np.round(np.minimum(1.0, frame[rules].to_numpy(dtype=float) @ w), 4)
    pred = score >= round(threshold, 4)
    y = frame["label"].to_numpy(dtype=bool)
    tp, fp = int((pred & y).sum()), int((pred & ~y).sum())
    fn, tn = int((~pred & y).sum()), int((~pred & ~y).sum())
    pos, neg = tp + fn, fp + tn
    return {"score": score, "pred": pred, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "detection": tp / pos if pos else 0.0, "fpr": fp / neg if neg else 0.0}


def _reset_whatif() -> None:
    st.session_state["wi_thr"] = float(get_settings().confidence_threshold)
    for r, v in RULE_WEIGHTS.items():
        st.session_state[f"wi_w_{r}"] = float(v)


def page_whatif(app) -> None:
    header("Policy what-if", "Re-score every screened request under a different threshold or rule weights - "
                             "no pipeline re-run.")
    frame, rules, labels_ok = whatif_frame(app)
    base_thr = float(get_settings().confidence_threshold)
    base_w = {r: float(RULE_WEIGHTS.get(r, 0.0)) for r in rules}
    if not labels_ok:
        st.info("Ground-truth labels are not available (PIPELINE_RESULTS.label_duplicate); "
                "detection and false-positive rates cannot be computed.")
    side, main = st.columns([1, 2])
    with side:
        st.markdown("**Policy levers**")
        st.button("Reset to current policy", on_click=_reset_whatif, key="wi_reset")
        thr = st.slider("Confidence threshold", 0.05, 1.0, base_thr, 0.01, key="wi_thr")
        weights = {r: st.slider(r, 0.0, 1.0, base_w[r], 0.01, key=f"wi_w_{r}") for r in rules}
    base = simulate(frame, rules, base_w, base_thr)
    sim = simulate(frame, rules, weights, thr)
    with main:
        k = st.columns(4)
        k[0].metric("Detection rate", f"{sim['detection']:.1%}",
                    f"{(sim['detection'] - base['detection']) * 100:+.1f} pp")
        k[1].metric("False-positive rate", f"{sim['fpr']:.1%}",
                    f"{(sim['fpr'] - base['fpr']) * 100:+.1f} pp", delta_color="inverse")
        k[2].metric("Escalated to STR", int(sim["pred"].sum()), int(sim["pred"].sum() - base["pred"].sum()))
        k[3].metric("Requests", len(frame))
        cm = pd.DataFrame({"Actual duplicate": [sim["tp"], sim["fn"]], "Actual clean": [sim["fp"], sim["tn"]]},
                          index=["Predicted HIGH (escalate)", "Predicted LOW (hold / clear)"])
        bcm = pd.DataFrame({"Actual duplicate": [base["tp"], base["fn"]], "Actual clean": [base["fp"], base["tn"]]},
                           index=cm.index)
        c1, c2 = st.columns(2)
        c1.markdown("**Confusion matrix - simulated**")
        c1.dataframe(cm, use_container_width=True)
        c2.markdown("**Delta vs current policy**")
        c2.dataframe(cm - bcm, use_container_width=True)
        st.caption(f"Current policy: threshold {base_thr:.2f}, default weights. Positive = scored HIGH band "
                   "(STR drafted); label = known duplicate in the synthetic data.")

        st.markdown("**Requests that flip band**")
        flip = sim["pred"] != base["pred"]
        if flip.any():
            f = frame.loc[flip, ["request_id", "label"]].copy()
            f["current score"], f["simulated score"] = base["score"][flip], sim["score"][flip]
            f["current band"] = np.where(base["pred"][flip], "HIGH", "LOW")
            f["simulated band"] = np.where(sim["pred"][flip], "HIGH", "LOW")
            f["label"] = np.where(f["label"], "duplicate", "clean") if labels_ok else "n/a"
            st.dataframe(f, use_container_width=True, hide_index=True)
        else:
            st.caption("No request changes band under the simulated policy.")

        st.markdown("**Marginal value per rule** (simulated policy with that rule's weight set to 0)")
        out = []
        for r in rules:
            alt = simulate(frame, rules, {**weights, r: 0.0}, thr)
            out.append({"rule": r, "weight": weights[r], "fires on": int(frame[r].sum()),
                        "detection if removed": alt["detection"],
                        "delta detection (pp)": (alt["detection"] - sim["detection"]) * 100,
                        "FPR if removed": alt["fpr"], "delta FPR (pp)": (alt["fpr"] - sim["fpr"]) * 100})
        st.dataframe(pd.DataFrame(out).round(4), use_container_width=True, hide_index=True)
    st.caption("Simulation only - changing live policy requires governed approval.")


# --------------------------------------------------------------------------- main
def main() -> None:
    st.set_page_config(page_title="Suraksha", page_icon="🛡️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.sidebar.markdown("### 🛡️ Suraksha")
    st.sidebar.caption("Duplicate-financing detector")
    page = st.sidebar.radio("Persona", PAGES, key="persona")
    mode, session = detect_backend()
    st.sidebar.caption(f"Backend: {mode}" + (" (Streamlit-in-Snowflake session)" if session is not None else ""))
    st.markdown('<div class="sk-banner">Synthetic data only - no real customers, banks or registry records.</div>',
                unsafe_allow_html=True)
    if mode == "snowflake":
        try:
            store = load_snowflake_store(session)
            rows = snowflake_rows(store)
        except Exception as exc:
            st.error(f"Could not read Snowflake: {exc}")
            return
        if not rows:
            st.info("No pipeline results yet. Run `CALL SURAKSHA.CORE.RUN_PIPELINE(42)` first "
                    "(after `CALL SURAKSHA.CORE.LOAD_SYNTH(42)`), then refresh.")
            return
        app = {"mode": "snowflake", "store": store, "engine": None, "rows": rows}
    else:
        app = load_app()
    {PAGES[0]: page_analyst, PAGES[1]: page_investigator, PAGES[2]: page_mlro, PAGES[3]: page_risk, PAGES[4]: page_whatif}[page](app)


main()
