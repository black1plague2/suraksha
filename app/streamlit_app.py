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

_APP = Path(__file__).resolve().parent  # app/ui.py lives beside this file (root SiS shim runs us via runpy)
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import ui  # noqa: E402  (theme CSS, status pills, formatters)
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
        if case is not None and case.status != CaseStatus.PENDING_APPROVAL:
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
        if case is not None and case.status != CaseStatus.PENDING_APPROVAL:
            return case.status.value
    return STATUS_LABEL[res.status]


def company_name(store, cid: str) -> str:
    return (store.get_company(cid) or {}).get("name", cid)


def plain_reason(app, rid: str) -> str:
    if app["mode"] == "snowflake":
        r = next(x for x in app["rows"] if x["request_id"] == rid)
        if r["pipeline_status"] == PipelineStatus.CLEAR:
            return "No matching pledge found at other consortium banks."
        score = f" Confidence {r['py_score']:.2f} ({r['py_band']})." if r.get("py_score") is not None else ""
        return "Flagged by the pipeline: " + (", ".join(r["py_rules"]) or "no rules recorded") + "." + score
    res = app["results"][rid]
    if res.status == PipelineStatus.CLEAR:
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


# dark text on pale nodes + mid-grey edges: legible on both light and dark Streamlit themes
_DOT_HEAD = ('digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", '
             'fillcolor="#E3F1EF", color="#0F766E", fontcolor="#12343B", fontname="Helvetica", fontsize=11]; '
             'edge [color="#7A8B99"];')


def draw_path(store, path) -> None:
    """Ownership path as node -> (relation) -> node graphviz diagram."""
    def label(node: str) -> str:
        kind, _, ident = node.partition(":")
        if kind == "company":
            return f"{(store.get_company(ident) or {}).get('name', ident)}\\n{ident}"
        return node

    dot = [_DOT_HEAD.replace("edge [color=\"#7A8B99\"]", 'edge [color="#7A8B99", fontcolor="#7A8B99", fontsize=9]')]
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
    dot = [_DOT_HEAD]
    for i, t in enumerate(edges_text):
        t = t.replace('"', "'")
        dot.append(f'n{i} [label="{t}"];')
        if i:
            dot.append(f"n{i-1} -> n{i};")
    dot.append("}")
    st.graphviz_chart("\n".join(dot))


NAV_LABEL = {PAGES[0]: "Requests", PAGES[1]: "Investigate", PAGES[2]: "Cases", PAGES[3]: "Overview", PAGES[4]: "What if"}
PAGE_PURPOSE = {
    PAGES[0]: "Every new financing request, checked against cargo already pledged at other banks.",
    PAGES[1]: "Ask who is linked to whom, in plain English.",
    PAGES[2]: "Review each flagged case and decide as a named person.",
    PAGES[3]: "Money held and queue health across the banks.",
    PAGES[4]: "See what a different threshold or rule weight would change. Simulation only.",
}


def header(title: str, sub: str = "") -> None:
    """Page title only: pages carry no explanatory intro (PAGE_PURPOSE is kept as documentation of each page)."""
    st.markdown(ui.page_header(title, ""), unsafe_allow_html=True)


def subhead(title: str) -> None:
    st.markdown(ui.section(title), unsafe_allow_html=True)


def pretty_requests(df: pd.DataFrame):
    """Friendly, display-only table: few columns, short amounts, soft status pills."""
    out = pd.DataFrame({
        "Request": df["request_id"], "Borrower": df["borrower"], "Cargo": df["commodity"],
        "Amount": [ui.fmt_compact(a, c) for a, c in zip(df["amount"], df["currency"])],
        "Status": [ui.status_label(x) for x in df["status"]],
    })
    return ui.style_status(out, "Status")


def analyst_kpis(app, df: pd.DataFrame) -> None:
    flagged = df[df["status"] != "CLEAR"]
    held = df[df["status"].isin(["PENDING_APPROVAL", "FILED"])].groupby("currency")["amount"].sum().sort_values(ascending=False)
    k = st.columns(5)
    k[0].metric("Requests screened", len(df))
    k[1].metric("Flagged", len(flagged))
    k[2].metric("Pending approval", int((df["status"] == "PENDING_APPROVAL").sum()))
    if held.empty:
        k[3].metric("Exposure held", "-")
    else:
        k[3].metric("Exposure held", " · ".join(ui.fmt_compact(v, c) for c, v in held.items()))
    try:
        frame, _rules, labels_ok = whatif_frame(app)
        flagged_ids = set(df.loc[df["status"] != "CLEAR", "request_id"])
        pred = frame["request_id"].isin(flagged_ids).to_numpy()
        y = frame["label"].to_numpy(dtype=bool)
        det = float((pred & y).sum()) / max(int(y.sum()), 1)
        fpr = float((pred & ~y).sum()) / max(int((~y).sum()), 1)
        k[4].metric("Detection · false positives", f"{det:.0%} · {fpr:.1%}" if labels_ok else "n/a"
                    if labels_ok else "No ground-truth labels available.")
    except Exception:
        k[4].metric("Detection · false positives", "n/a")


# --------------------------------------------------------------------------- pages
def page_analyst(app) -> None:
    header("Requests", PAGE_PURPOSE[PAGES[0]])
    df = requests_df(app)
    analyst_kpis(app, df)
    st.write("")
    c1, c2 = st.columns(2)
    bank = c1.selectbox("Bank", ["All"] + sorted(df["bank"].unique()), key="analyst_bank")
    stat = c2.multiselect("Status", sorted(df["status"].unique()), default=[], key="analyst_status",
                          format_func=ui.status_label)
    view = df
    if bank != "All":
        view = view[view["bank"] == bank]
    if stat:
        view = view[view["status"].isin(stat)]
    if view.empty:
        st.info("No matching requests.")
        return
    st.dataframe(pretty_requests(view), use_container_width=True, hide_index=True)
    subhead("Detail")
    rid = st.selectbox("Open request", list(view["request_id"]), key="analyst_pick")
    row = df[df["request_id"] == rid].iloc[0]
    clear = row["status"] == "CLEAR"
    st.markdown(ui.card("", f'<p>{ui.status_pill(row["status"])} &nbsp;<span class="sk-mono">{ui.esc(rid)}</span></p>' +
                        ui.facts([("Borrower", row["borrower"]), ("Bank", ui.bank_label(row["bank"])),
                                  ("Amount", ui.fmt_money(row["amount"], row["currency"])),
                                  ("Submitted", row["submitted"])]) +
                        f'<p style="margin-top:1rem">{ui.esc(plain_reason(app, rid))}</p>'), unsafe_allow_html=True)
    if app["mode"] == "snowflake":
        res = None
        f = app["store"].get_fields(rid)
    else:
        res = app["results"][rid]
        f = res.fields
    if f is None:
        st.caption("No details stored.")
        return
    subhead("Fields")
    st.dataframe(fields_table(f).rename(columns={"field": "Field", "value": "Value", "source": "Source",
                                                 "snippet": "Source line"}),
                 use_container_width=True, hide_index=True)
    if res is None:
        r = next(x for x in app["rows"] if x["request_id"] == rid)
        if r["py_rules"]:
            subhead("Evidence (rules fired)")
            st.dataframe(pd.DataFrame({"Rule": r["py_rules"]}), use_container_width=True, hide_index=True)
        return
    if res.confidence and res.confidence.evidence:
        subhead("Evidence")
        st.dataframe(pd.DataFrame([{"Rule": e.rule_id, "Weight": e.weight, "Finding": e.description}
                                   for e in res.confidence.evidence]), use_container_width=True, hide_index=True,
                     **_weight_cfg())
    if res.confidence and res.confidence.missing:
        st.warning("Evidence needed before this can proceed:\n\n" + "\n".join(f"- {m}" for m in res.confidence.missing))


def _weight_cfg() -> dict:
    """column_config for a 0-1 'Weight' column (bar + number); {} when the Streamlit build lacks it."""
    try:
        return {"column_config": {"Weight": st.column_config.ProgressColumn("Weight", min_value=0.0, max_value=1.0,
                                                                            format="%.2f")}}
    except Exception:
        return {}


def page_investigator(app) -> None:
    store = app["store"]
    header("Investigate", PAGE_PURPOSE[PAGES[1]])
    comps = store.list_companies()
    if len(comps) >= 2:
        a, b = comps[0]["name"], comps[1]["name"]
        examples = [f"Who else is linked to {a}?", f"Directors of {a}", f"Path between {a} and {b}"]
        for col, ex in zip(st.columns(len(examples)), examples):
            col.button(ex, key=f"inv_ex_{examples.index(ex)}", use_container_width=True,
                       on_click=lambda e=ex: st.session_state.__setitem__("inv_q", e))
    else:
        st.caption("No companies loaded.")
    q = st.text_input("Question", key="inv_q", placeholder="who else is linked to <company>?")
    if q.strip():
        try:
            ans = linkage.answer(q, store)
        except Exception as exc:  # show, never crash
            st.error(f"Could not answer: {exc}. Try one of the example questions.")
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
                subhead("Rows")
                st.dataframe(pd.DataFrame(flat), use_container_width=True, hide_index=True)
            if ans["citations"]:
                st.markdown(ui.section(f"Sources ({len(ans['citations'])})"), unsafe_allow_html=True)
                st.markdown(ui.citation_chips(ans["citations"]), unsafe_allow_html=True)
                with st.expander("Citation details"):
                    st.dataframe(citation_rows(ans["citations"]), use_container_width=True, hide_index=True)

    st.divider()
    subhead("Needs evidence")
    if app["mode"] == "snowflake":
        weak_rows = [r for r in app["rows"] if r["pipeline_status"] == PipelineStatus.NEED_MORE_EVIDENCE]
        if not weak_rows:
            st.caption("None waiting.")
        for r in weak_rows:
            score = f"{r['py_score']:.2f}" if r.get("py_score") is not None else "-"
            with st.expander(f"{r['request_id']} · score {score} ({r.get('py_band')})"):
                st.write(plain_reason(app, r["request_id"]))
        return
    weak = [(rid, r) for rid, r in app["results"].items() if r.status == PipelineStatus.NEED_MORE_EVIDENCE]
    if not weak:
        st.caption("None waiting.")
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


RULE_PLAIN = {
    "R_EXACT_HASH": "The very same cargo is already pledged at another bank",
    "R_FUZZY_MATCH": "Almost the same cargo is already pledged at another bank",
    "R_SHARED_UBO": "The two borrowers have the same ultimate owner",
    "R_SHARED_DIRECTOR": "The two borrowers share a director",
    "R_CORP_OWNERSHIP": "One borrower owns the other",
    "R_SAME_ADDRESS": "Both borrowers are registered at the same address",
    "R_SAME_PHONE": "Both borrowers use the same phone number",
    "R_TIMING_OVERLAP": "The second loan was requested soon after the first",
    "R_NO_VESSEL_CALL": "The ship has no recorded call at the loading port",
    "R_DOC_MISMATCH": "The shipping papers disagree with each other",
}
_REL_PHRASE = {  # relation -> (walking src->dst, walking dst->src)
    "DIRECTOR_OF": ("director of", "has director"), "SHAREHOLDER_OF": ("shareholder of", "held by"),
    "UBO_OF": ("owner of", "owned by"), "OWNS": ("owns", "owned by"),
    "REGISTERED_AT": ("registered at", "address of"), "HAS_PHONE": ("has phone", "phone of"),
}
_BOTH = {("owned by", "owner of"): "owns both", ("has director", "director of"): "director of both",
         ("held by", "shareholder of"): "shareholder in both", ("address of", "registered at"): "same address",
         ("phone of", "has phone"): "same phone"}
_ACTION_PLAIN = {
    "REQUEST_RECEIVED": "Financing request received",
    "MATCH_FOUND": "Matching cargo found at another bank",
    "EVIDENCE_SCORED": "Evidence scored",
    "CASE_OPENED": "Case opened and report drafted",
    "CASE_FILED": "Report filed, loan hold recommended",
    "CASE_CLOSED": "Case closed with no filing",
}
_JURIS_PLAIN = {"Generic": "Plain", "FIU-IND": "India", "UAE-goAML": "UAE"}


def request_info(app, rid: str) -> dict:
    """Borrower / bank / amount / date for one request, from either backend."""
    store = app["store"]
    if app["mode"] == "snowflake":
        r = next((x for x in app["rows"] if x["request_id"] == rid), {})
        sub = r.get("submitted_at")
        return {"borrower_id": r.get("borrower_id"), "borrower": company_name(store, r.get("borrower_id") or "?"),
                "bank": r.get("bank_id") or "?", "amount": float(r.get("amount") or 0),
                "currency": r.get("currency") or "", "submitted": sub.strftime("%d %b %Y") if sub is not None else "-"}
    req = store.get_request(rid)
    if req is None:
        return {"borrower_id": None, "borrower": "?", "bank": "?", "amount": 0.0, "currency": "", "submitted": "-"}
    return {"borrower_id": req.borrower_id, "borrower": company_name(store, req.borrower_id), "bank": req.bank_id,
            "amount": req.amount, "currency": req.currency, "submitted": req.submitted_at.strftime("%d %b %Y")}


def audit_status(app) -> tuple[bool, int]:
    """(chain_ok, n_records). Memory: recomputed each run. Snowflake: cached per session, cleared on decisions."""
    if app["mode"] == "snowflake":
        cached = st.session_state.get("_audit_sf")
        if cached is None:
            try:
                ok, n, breaks = audit_verify_snowflake(app["store"])
                cached = (bool(ok and not breaks), n)
            except Exception:
                cached = (False, 0)
            st.session_state["_audit_sf"] = cached
        return cached
    try:
        return bool(app["engine"].audit.verify()), len(app["store"].list_audit())
    except Exception:
        return False, 0


def _person_or_company(store, node: str) -> str:
    kind, _, ident = node.partition(":")
    if kind == "company":
        return (store.get_company(ident) or {}).get("name", ident)
    if kind == "person":
        try:
            p = store.get_person(ident)
        except Exception:
            p = None
        return (p or {}).get("name") or ident
    return f"Shared {kind} {ident}" if kind in ("phone", "address") else node


def _walk_path(store, path):
    """Path edges -> ([node dicts], [relation phrases]) walking from path.from_company in either edge direction."""
    cur = f"company:{path.from_company}"
    cards, rels = [{"label": _person_or_company(store, cur), "hot": False}], []
    for e in path.edges:
        fwd = e.src == cur
        nxt = e.dst if fwd else e.src
        phr = _REL_PHRASE.get(e.relation, (e.relation.replace("_", " ").lower(),) * 2)
        rels.append(phr[0] if fwd else phr[1])
        cards.append({"label": _person_or_company(store, nxt), "hot": True})
        cur = nxt
    cards[-1]["hot"] = False
    if len(cards) == 3:
        cards[1]["sub"] = _BOTH.get((rels[0], rels[1]), "")
    return cards, rels


def vessel_answer(app, f, fired: set[str]) -> str:
    """Yes / No / Not in records for 'Did the ship really call there?'."""
    if "R_NO_VESSEL_CALL" in fired:
        return "No"
    store = app["store"]
    try:
        if f is not None and f.vessel and f.voyage and store.vessel_calls(f.vessel, f.voyage):
            return "Yes"
    except Exception:
        pass
    return "Not in records"


def render_hero(app, case) -> None:
    """Back link, one-sentence h1, one lead paragraph, one grey meta line."""
    store, rid = app["store"], case.request_id
    res = app["results"].get(rid) if app["mode"] == "memory" else None
    inv = res.investigation if res else None
    f = res.fields if res else store.get_fields(rid)
    info = request_info(app, rid)
    rows, _score, _band = rule_rows(app, rid)
    fired = {r["rule"] for r in rows}
    cargo = (f.commodity if f is not None and f.commodity else "").lower()
    if fired & {"R_EXACT_HASH", "R_FUZZY_MATCH"}:
        h1 = f"Cargo already pledged at {ui.bank_label(inv.match.entry.bank_id)}" if inv is not None else "Cargo already pledged elsewhere"
    elif "R_NO_VESSEL_CALL" in fired:
        h1 = "Possible phantom cargo"
    elif "R_DOC_MISMATCH" in fired:
        h1 = "Documents don't agree"
    else:
        h1 = "Needs more evidence"
    meta = [f"{ui.fmt_compact(info['amount'], info['currency'])} at {ui.bank_label(info['bank'])}"]
    if f is not None:
        if f.commodity:
            meta.append(ui.calm(f.commodity))
        if f.quantity is not None:
            meta.append(f"{f.quantity:,.1f} {f.quantity_unit or ''}".strip())
        if f.vessel:
            meta.append(f"{ui.calm(f.vessel, vessel=True)}, voyage {f.voyage}" if f.voyage else ui.calm(f.vessel, vessel=True))
        if f.port_of_loading and f.port_of_discharge:
            meta.append(f"{ui.calm(f.port_of_loading)} → {ui.calm(f.port_of_discharge)}")
    meta.append(rid)
    st.markdown(ui.hero(h1, "") + f'<div class="sk-meta">{ui.esc(" · ".join(meta))}</div>', unsafe_allow_html=True)


def render_case_card(app, case, rows) -> None:
    """ONE white card, three hairline-separated sections."""
    store, rid = app["store"], case.request_id
    res = app["results"].get(rid) if app["mode"] == "memory" else None
    inv = res.investigation if res else None
    info = request_info(app, rid)
    thr = float(get_settings().confidence_threshold)
    secs = []

    if inv is not None:
        m, days = inv.match, inv.timing_overlap_days
        other = company_name(store, inv.counterparty_company_id) if inv.counterparty_company_id else "Borrower not identified"
        hashes = ", ".join(f"{m.entry.keys.get(k, '')[:8]}…" for k in m.matched_keys) or "-"
        kind = "Exactly the same cargo" if m.match_type.value == "EXACT" else f"Near-identical cargo (similarity {m.similarity:.2f})"
        secs.append(("Two banks, one cargo", ui.two(
            ui.panel(f"Today · {ui.bank_label(info['bank'])}", info["borrower"],
                     [f"Asked for {ui.fmt_money(info['amount'], info['currency'])}", f"Submitted {info['submitted']}"]),
            ui.panel(("Earlier" if days is None else f"{days} days earlier") + f" · {ui.bank_label(m.entry.bank_id)}", other,
                     [f"Pledged {m.entry.pledged_at.strftime('%d %b %Y')}", kind])) ))
    elif app["mode"] == "snowflake":
        facts_row = snowflake_facts(store, rid)
        txt = (f"Match type {facts_row.get('match_type')}; timing overlap {facts_row.get('timing_overlap_days')} day(s)."
               if facts_row else "Match details are not available for this case.")
        secs.append(("Two banks, one cargo", f"<p>{ui.esc(txt)}</p>"))

    paths = [p for p in (inv.paths if inv else []) if p.edges]
    # Headline the STRONGEST link, not the shortest route (a shared phone must not beat a shared owner).
    _rank = {"UBO_OF": 0, "OWNS": 1, "DIRECTOR_OF": 2, "SHAREHOLDER_OF": 3, "REGISTERED_AT": 4, "HAS_PHONE": 5}
    paths.sort(key=lambda p: (max(_rank.get(e.relation, 6) for e in p.edges), len(p.edges)))
    fired_rules = {r["rule"] for r in rows}
    link_title = ("Shared owner" if "R_SHARED_UBO" in fired_rules else
                  "Shared director" if "R_SHARED_DIRECTOR" in fired_rules else "Linked companies")
    if paths:
        cards, rels = _walk_path(store, paths[0])
        if len(cards) == 3:  # name the section after what actually connects the pair
            mid = cards[1]["label"]
            link_title = ("Shared phone" if mid.startswith("Shared phone") else "Shared address" if mid.startswith("Shared address")
                          else "Shared owner" if any(r in ("owned by", "owner of", "owns") for r in rels)
                          else "Shared director" if any("director" in r for r in rels) else "Linked companies")
        extra = []
        if inv.shared_addresses:
            extra.append("They share an address: " + ", ".join(inv.shared_addresses))
        if inv.shared_phones:
            extra.append("They share a phone number: " + ", ".join(inv.shared_phones))
        secs.append((link_title, ui.node_chain(cards, rels) +
                     ""))
    else:
        if inv is not None and inv.counterparty_company_id == inv.borrower_id:
            txt = "It is the same legal entity at both banks, so no ownership link is needed."
        elif app["mode"] == "snowflake":
            txt = "Ownership walks run in the pipeline. Use Investigate for live questions."
        else:
            txt = "No ownership link was found between the two borrowers."
        secs.append((link_title, f"<p>{ui.esc(txt)}</p>"))

    if rows:
        total = sum(r["weight"] or 0 for r in rows)
        sc = min(1.0, total)
        secs.append(("Score", ui.score_rows(
            [(RULE_PLAIN.get(r["rule"], r["rule"]), "", f"+{(r['weight'] or 0):.2f}") for r in rows],
            f"{sc:.2f}" + (f" (capped from {total:.2f})" if total > 1.0 + 1e-9 else ""))))
    else:
        secs.append(("Score", "<p>No rules fired.</p>"))
    st.markdown(ui.sections_card(secs), unsafe_allow_html=True)
    if paths and st.checkbox("Show the link as a diagram", key=f"graph_{case.case_id}"):
        draw_path(store, paths[0])


def render_report(app, case, draft) -> None:
    """Row-link card (expander) with the drafted report, and an All-details expander with the documents."""
    store, rid = app["store"], case.request_id
    res = app["results"].get(rid) if app["mode"] == "memory" else None
    with st.expander("Drafted report", expanded=False):
        if draft is None:
            st.caption("No report.")
        else:
            juris = None
            if report_templates is not None:
                opts = ["Generic"] + list(getattr(report_templates, "JURISDICTIONS", ()))
                pick = st.radio("Format", opts, horizontal=True, key=f"juris_{case.case_id}",
                                format_func=lambda o: _JURIS_PLAIN.get(o, o))
                juris = None if pick == "Generic" else pick
            st.markdown(md_for_streamlit(str_markdown(draft, juris)))
            seen, cits = set(), []
            for sec in draft.sections:
                for sent in sec.sentences:
                    for c in sent.citations:
                        if (c.kind, c.ref, c.page) not in seen:
                            seen.add((c.kind, c.ref, c.page))
                            cits.append(c)
            if cits:
                kinds = Counter(c.kind.value for c in cits)
                st.markdown(ui.citation_chips(cits), unsafe_allow_html=True)
    with st.expander("All document details", expanded=False):
        f = res.fields if res else store.get_fields(rid)
        if f is None:
            st.caption("No details stored.")
        else:
            st.dataframe(fields_table(f).rename(columns={"field": "Field", "value": "Value", "source": "Source",
                                                         "snippet": "Source line"}),
                         use_container_width=True, hide_index=True)
            rows, _s, _b = rule_rows(app, rid)
            ans = vessel_answer(app, f, {r["rule"] for r in rows})
            st.caption(f"Port call at loading port: {ans}")


def _evidence_block(score, thr: float) -> str:
    tone = "bad" if (score is not None and score >= round(thr, 4)) else "warn"
    sc = "-" if score is None else f"{score:.2f}"
    return (f'<div class="sk-ev"><small>Evidence</small><div class="sk-score {tone}">{sc}</div>'
            f'{ui.score_bar(score, thr)}</div>')


def render_decision(app, case) -> None:
    """Sticky card: evidence score, decision form (or recorded decision), log note."""
    cid, engine, store = case.case_id, app.get("engine"), app["store"]
    _rows, score, _band = rule_rows(app, case.request_id)
    thr = float(get_settings().confidence_threshold)
    ok, n = audit_status(app)
    log_note = (f'<div class="sk-form-note"><span class="sk-tick {"ok" if ok else "bad"}">'
                f'{"✓ Log verified" if ok else "Log check failed"} · {n} records</span></div>')
    if case.status == CaseStatus.PENDING_APPROVAL:
        with st.form(f"decide_{cid}"):
            st.markdown(_evidence_block(score, thr) + '<div class="sk-h2">Decision</div>', unsafe_allow_html=True)
            officer = st.text_input("Your name", key=f"officer_{cid}")
            reason = st.text_area("Note", key=f"reason_{cid}", placeholder="Required to close")
            approve = st.form_submit_button("File report and hold the loan", type="primary", use_container_width=True)
            reject = st.form_submit_button("Close the case", use_container_width=True)
            st.markdown(log_note, unsafe_allow_html=True)
        if approve or reject:
            decision = Decision.APPROVE if approve else Decision.REJECT
            try:
                if app["mode"] == "snowflake":
                    out = decide_snowflake(store, cid, decision, officer, reason)
                else:
                    engine.decide(cid, decision, officer, reason)
            except Exception as exc:
                st.error(str(exc))  # the server's refusal, verbatim
            else:
                st.session_state.pop("_audit_sf", None)
                if app["mode"] == "snowflake":
                    st.session_state["decided_msg"] = f"DECIDE_CASE({cid}): {json.dumps(out, default=str)}"
                else:
                    st.session_state["decided_msg"] = (
                        f"Case {cid} {'filed' if approve else 'closed'} by {officer.strip()}.")
                st.rerun()
    else:
        when = f"{case.decided_at:%d %b %Y %H:%M} UTC" if case.decided_at else "-"
        st.markdown(ui.card("", _evidence_block(score, thr) + '<div class="sk-h2">Decision recorded</div>' + ui.facts([
            ("Decided by", case.decided_by or "-"), ("When", when), ("Note", case.reason or "No note")]) + log_note),
            unsafe_allow_html=True)


def _who(actor: str) -> str:
    kind, _, name = (actor or "").partition(":")
    return "by the system" if kind == "system" else f"by {name or actor}"


def render_activity(app, case) -> None:
    store = app["store"]
    recs = store.list_audit(case.case_id) + store.list_audit(case.request_id)
    recs.sort(key=lambda r: r.seq)
    items = [(_ACTION_PLAIN.get(r.action, r.action.replace("_", " ").capitalize()),
              f"{_who(r.actor)} · {r.at.strftime('%d %b %H:%M')}") for r in recs]
    with st.expander("Audit trail"):
        if items:
            st.markdown(ui.timeline(items), unsafe_allow_html=True)
        _verify_button(app, store)


def _verify_button(app, store) -> None:
    if st.button("Verify audit chain", key="verify"):
        if app["mode"] == "snowflake":
            ok, n, breaks = audit_verify_snowflake(store)
            sql_txt = "unavailable" if breaks is None else f"{breaks} break(s)"
            if ok and not breaks:
                st.success(f"Audit chain intact ({n} records recomputed in Python). CORE.V_AUDIT_VERIFY: {sql_txt}.")
            else:
                st.error(f"Audit chain verification FAILED (python recompute ok={ok}); CORE.V_AUDIT_VERIFY: {sql_txt}.")
        elif app["engine"].audit.verify():
            st.success(f"Audit chain intact ({len(store.list_audit())} records, hashes verified).")
        else:
            st.error("Audit chain verification FAILED - tampering or gap detected.")


def page_mlro(app) -> None:
    store = app["store"]
    if st.session_state.get("decided_msg"):
        st.success(st.session_state.pop("decided_msg"))
    cases = store.list_cases()
    pending = [c for c in cases if c.status == CaseStatus.PENDING_APPROVAL]
    if not cases:
        header("Cases")
        st.info("No cases yet.")
        return
    c1, c2 = st.columns([4, 1])
    only_pending = c2.checkbox("Pending only", value=True, key="mlro_pending")
    shown = pending if only_pending else cases
    if not shown:
        header("Cases")
        st.success("No pending cases. Untick Pending only to look at decided cases.")
        return
    ids = [c.case_id for c in shown]
    by_id = {c.case_id: c for c in shown}
    cid = c1.selectbox("Case", ids, key="mlro_case",
                       format_func=lambda i: f"{by_id[i].request_id} · {request_info(app, by_id[i].request_id)['borrower']}")
    case = store.get_case(cid)
    draft = store.get_report(case.report_id)
    left, right = st.columns([5, 3], gap="large")
    with left:
        st.markdown(ui.breadcrumb(f"Case {ids.index(cid) + 1} of {len(ids)} · "
                                  f"{'waiting for you' if only_pending else 'shown'}"), unsafe_allow_html=True)
        render_hero(app, case)
        rows, _s, _b = rule_rows(app, case.request_id)
        render_case_card(app, case, rows)
        render_report(app, case, draft)
        with st.expander("See all cases"):
            st.dataframe(ui.style_status(pd.DataFrame([{
                "Case": c.case_id, "Request": c.request_id, "Status": ui.status_label(c.status.value),
                "Decided by": c.decided_by or "-"} for c in shown]), "Status"), use_container_width=True, hide_index=True)
    with right:
        st.markdown('<span class="sk-sticky"></span>', unsafe_allow_html=True)
        render_decision(app, case)
        render_activity(app, case)


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
    header("Overview", PAGE_PURPOSE[PAGES[3]])
    df = requests_df(app)
    df["is_flagged"] = df["status"] != "CLEAR"
    df["exposure"] = df["amount"].where(df["is_flagged"], 0.0)
    flagged = df[df["is_flagged"]]
    k = st.columns(4)
    k[0].metric("Requests", len(df))
    k[1].metric("Flagged", len(flagged))
    k[2].metric("Pending approval", int((df["status"] == "PENDING_APPROVAL").sum()))
    k[3].metric("Filed", int((df["status"] == "FILED").sum()))

    subhead("Metrics")
    held = df[df["status"].isin(["PENDING_APPROVAL", "FILED"])].groupby("currency")["amount"].sum()
    m = st.columns(3)
    with m[0]:
        st.markdown("**Exposure held** (pending + filed)")
        if held.empty:
            st.caption("Nothing held.")
        for cur, amt in held.items():
            st.metric(cur or "?", f"{amt:,.0f}")
    try:
        ttf = time_to_finding(app)
    except Exception:
        ttf = pd.Series(dtype=float)
    med = "-" if ttf.empty else (f"{ttf.median() * 1000:,.0f} ms" if ttf.median() < 2 else f"{ttf.median():,.1f} s")
    m[1].metric("Median time: request to drafted finding", med)
    m[2].metric("Analyst queue (need more evidence)", int((df["status"] == "HOLD-for-evidence").sum()))

    subhead("Flagged exposure")
    exp = flagged.groupby("currency", as_index=False)["amount"].sum()
    if exp.empty:
        st.caption("No exposure.")
    else:
        for col, (_, r) in zip(st.columns(len(exp)), exp.iterrows()):
            col.metric(r["currency"], f"{r['amount']:,.0f}")
    c1, c2 = st.columns(2)
    for col, key, title in ((c1, "bank", "By bank"), (c2, "commodity", "By commodity")):
        with col:
            subhead(title)
            g = df.groupby(key).agg(requests=("request_id", "count"), flagged=("is_flagged", "sum"),
                                    flagged_exposure=("exposure", "sum")).reset_index()
            st.dataframe(g.rename(columns={key: key.title(), "requests": "Requests", "flagged": "Flagged",
                                           "flagged_exposure": "Flagged exposure"}),
                         use_container_width=True, hide_index=True)
            st.bar_chart(g.set_index(key)[["requests", "flagged"]])
    subhead("Daily summary")
    daily = df.groupby("submitted").agg(requests=("request_id", "count"), flagged=("is_flagged", "sum"),
                                        flagged_exposure=("exposure", "sum")).reset_index()
    st.dataframe(daily.sort_values("submitted", ascending=False).rename(columns={
        "submitted": "Date", "requests": "Requests", "flagged": "Flagged", "flagged_exposure": "Flagged exposure"}),
        use_container_width=True, hide_index=True)


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
    header("What if", PAGE_PURPOSE[PAGES[4]])
    frame, rules, labels_ok = whatif_frame(app)
    base_thr = float(get_settings().confidence_threshold)
    base_w = {r: float(RULE_WEIGHTS.get(r, 0.0)) for r in rules}
    if not labels_ok:
        st.info("Labels not available.")
    side, main = st.columns([1, 2])
    with side:
        with st.expander("Policy levers", expanded=True):
            st.button("Reset to current policy", on_click=_reset_whatif, key="wi_reset", use_container_width=True)
            thr = st.slider("Report threshold", 0.05, 1.0, base_thr, 0.01, key="wi_thr")
            weights = {r: st.slider(RULE_PLAIN.get(r, r), 0.0, 1.0, base_w[r], 0.01, key=f"wi_w_{r}") for r in rules}
    base = simulate(frame, rules, base_w, base_thr)
    sim = simulate(frame, rules, weights, thr)
    with main:
        k = st.columns(4)
        k[0].metric("Duplicates reported", f"{sim['detection']:.1%}",
                    f"{(sim['detection'] - base['detection']) * 100:+.1f} pp")
        k[1].metric("Clean requests reported", f"{sim['fpr']:.1%}",
                    f"{(sim['fpr'] - base['fpr']) * 100:+.1f} pp", delta_color="inverse")
        k[2].metric("Reports drafted", int(sim["pred"].sum()), int(sim["pred"].sum() - base["pred"].sum()))
        k[3].metric("Requests", len(frame))
        cm = pd.DataFrame({"Duplicate": [sim["tp"], sim["fn"]], "Clean": [sim["fp"], sim["tn"]]},
                          index=["Report drafted", "No report"])
        st.markdown("**Outcomes**")
        st.dataframe(cm, use_container_width=True)

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
            st.caption("No changes.")

        st.markdown("**Value of each rule**")
        out = []
        for r in rules:
            alt = simulate(frame, rules, {**weights, r: 0.0}, thr)
            out.append({"Rule": RULE_PLAIN.get(r, r), "Weight": round(weights[r], 2), "Fires on": int(frame[r].sum()),
                        "Reports lost without it": int(sim["tp"] - alt["tp"])})
        st.dataframe(pd.DataFrame(out), use_container_width=True, hide_index=True)
    


# --------------------------------------------------------------------------- main
def page_counts(app) -> dict[str, int]:
    """Small count shown beside a nav item: flagged requests (analyst), pending cases (MLRO)."""
    try:
        df = requests_df(app)
        return {PAGES[0]: int((df["status"] != "CLEAR").sum()),
                PAGES[2]: int((df["status"] == "PENDING_APPROVAL").sum())}
    except Exception:
        return {}


def main() -> None:
    st.set_page_config(page_title="Suraksha", page_icon="🛡️", layout="wide", initial_sidebar_state="collapsed")
    st.markdown(ui.CSS, unsafe_allow_html=True)
    mode, session = detect_backend()
    if mode != "snowflake":  # web fonts only locally; Streamlit-in-Snowflake blocks them and uses the fallback stacks
        st.markdown(ui.FONT_CSS, unsafe_allow_html=True)
    app, problem = None, None
    if mode == "snowflake":
        try:
            store = load_snowflake_store(session)
            rows = snowflake_rows(store)
            if rows:
                app = {"mode": "snowflake", "store": store, "engine": None, "rows": rows}
            else:
                problem = ("info", "No pipeline results yet. Run `CALL SURAKSHA.CORE.RUN_PIPELINE(42)` first "
                                   "(after `CALL SURAKSHA.CORE.LOAD_SYNTH(42)`), then refresh.")
        except Exception as exc:
            problem = ("error", f"Could not read Snowflake: {exc}")
    else:
        app = load_app()
    counts = page_counts(app) if app else {}
    nav = {p: (f"{NAV_LABEL[p]} · {counts[p]}" if p == PAGES[2] and p in counts else NAV_LABEL[p]) for p in PAGES}
    c_brand, c_nav, c_badge = st.columns([1.9, 6.4, 2.2])
    c_brand.markdown(ui.brand_mark(), unsafe_allow_html=True)
    page = c_nav.radio("Page", PAGES, key="persona", horizontal=True, label_visibility="collapsed",
                       format_func=lambda p: nav[p])
    c_badge.markdown(ui.badges(mode == "snowflake"), unsafe_allow_html=True)
    if problem is not None:
        getattr(st, problem[0])(problem[1])
        return
    {PAGES[0]: page_analyst, PAGES[1]: page_investigator, PAGES[2]: page_mlro, PAGES[3]: page_risk, PAGES[4]: page_whatif}[page](app)


main()
