"""Suraksha - duplicate-financing detector. One Streamlit app, four personas.

Run locally:   streamlit run app/streamlit_app.py
Backend:       SURAKSHA_BACKEND=memory (default; synthetic data generated in-process).
Imports are limited to streamlit + pandas + stdlib + suraksha so the file can run
as Streamlit-in-Snowflake later.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

# Allow `streamlit run app/streamlit_app.py` from a source checkout without installing.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from suraksha.agents import linkage  # noqa: E402
from suraksha.agents.investigator import describe_edge  # noqa: E402
from suraksha.agents.report import render_markdown  # noqa: E402
from suraksha.models import CaseStatus, Decision, PipelineStatus  # noqa: E402
from suraksha.pipeline import Suraksha  # noqa: E402

PAGES = ["Trade-ops analyst", "Investigator", "Compliance officer (MLRO)", "Risk head"]
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
    return {"store": store, "engine": engine, "requests": ds.requests, "results": results}


def effective_status(app, rid: str) -> str:
    res = app["results"][rid]
    if res.case is not None:
        case = app["store"].get_case(res.case.case_id)
        if case is not None and case.status is not CaseStatus.PENDING_APPROVAL:
            return case.status.value
    return STATUS_LABEL[res.status]


def company_name(store, cid: str) -> str:
    return (store.get_company(cid) or {}).get("name", cid)


def plain_reason(app, rid: str) -> str:
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
    res = app["results"][rid]
    row = df[df["request_id"] == rid].iloc[0]
    st.subheader(f"{rid}  ·  {row['badge']}")
    st.info(plain_reason(app, rid))
    f = res.fields
    if f is None:
        return
    st.markdown("**Extracted fields (with source)**")
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
    st.dataframe(pd.DataFrame(out), use_container_width=True, hide_index=True)
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
    weak = [(rid, r) for rid, r in app["results"].items() if r.status is PipelineStatus.NEED_MORE_EVIDENCE]
    if not weak:
        st.caption("None.")
    for rid, r in weak:
        conf = r.confidence
        with st.expander(f"{rid} · {r.investigation.match.entry.bank_id} match · score {conf.score:.2f} (LOW)"):
            st.write(plain_reason(app, rid))
            st.markdown("**Missing evidence:**")
            for m in conf.missing:
                st.markdown(f"- {m}")
            for p in r.investigation.paths[:1]:
                if p.edges:
                    draw_chain([describe_edge(e) for e in p.edges])


def page_mlro(app) -> None:
    store, engine = app["store"], app["engine"]
    header("Compliance officer (MLRO)", "Review the drafted STR, then approve or reject as a named officer.")
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
    if draft:
        with st.container(border=True):
            st.markdown(md_for_streamlit(render_markdown(draft)))
    if case.status is CaseStatus.PENDING_APPROVAL:
        with st.form(f"decide_{cid}"):
            officer = st.text_input("Officer name (required)", key=f"officer_{cid}")
            reason = st.text_area("Reason (required to reject)", key=f"reason_{cid}")
            b1, b2 = st.columns(2)
            approve = b1.form_submit_button("Approve - file case & recommend hold", type="primary")
            reject = b2.form_submit_button("Reject - close case")
        if approve or reject:
            try:
                engine.decide(cid, Decision.APPROVE if approve else Decision.REJECT, officer, reason)
            except Exception as exc:
                st.error(str(exc))
            else:
                st.success(f"Case {cid} {'filed' if approve else 'closed'} by {officer.strip()}.")
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
        if engine.audit.verify():
            st.success(f"Audit chain intact ({len(store.list_audit())} records, hashes verified).")
        else:
            st.error("Audit chain verification FAILED - tampering or gap detected.")


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


# --------------------------------------------------------------------------- main
def main() -> None:
    st.set_page_config(page_title="Suraksha", page_icon="🛡️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.sidebar.markdown("### 🛡️ Suraksha")
    st.sidebar.caption("Duplicate-financing detector")
    page = st.sidebar.radio("Persona", PAGES, key="persona")
    backend = os.getenv("SURAKSHA_BACKEND", "memory").lower()
    st.sidebar.caption(f"Backend: {backend}")
    if backend != "memory":
        st.sidebar.warning("Only the 'memory' backend is wired into this app; using it.")
    st.markdown('<div class="sk-banner">Synthetic data only - no real customers, banks or registry records.</div>',
                unsafe_allow_html=True)
    app = load_app()
    {PAGES[0]: page_analyst, PAGES[1]: page_investigator, PAGES[2]: page_mlro, PAGES[3]: page_risk}[page](app)


main()
