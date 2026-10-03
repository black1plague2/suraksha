"""Presentation helpers for the Suraksha Streamlit app: theme CSS, soft status pills, formatters, small HTML cards.

Pure functions + one CSS string; no data access, no external assets (system font stacks only), nothing newer than
what Streamlit-in-Snowflake ships. Every builder returns a SINGLE-LINE html string (markdown ends an html block at a
blank line and treats 4-space indents as code), and every dynamic value is escaped.

Look: cool grey-blue page, white borderless cards (navy-tinted shadows, light from above), navy accent, apricot instead of alarm red.
The page background/ink are set explicitly so the app also reads correctly if a viewer forces the dark theme.
"""
from __future__ import annotations

import html

import pandas as pd

# internal status value -> (display label, tone)
STATUS_META = {
    "CLEAR": ("Clear", "ok"),
    "HOLD-for-evidence": ("Needs evidence", "warn"),
    "PENDING_APPROVAL": ("Waiting for review", "bad"),
    "FILED": ("Filed", "neutral"),
    "CLOSED": ("Closed", "neutral"),
}
_TONE_STYLE = {}  # filled below once the palette is known (text, background)


# One token dict per palette; change PALETTE to switch the whole app. No hex values outside this dict
# (apart from the two fixed status hues, amber for "needs evidence" and green for "clear").
PALETTES = {
    "Midnight & apricot": dict(bg="#EEF1F5", surface="#FFFFFF", ink="#1C2030", muted="#4B5162", faint="#6B7180",
                               line="#E1E6ED", border="#CBD2DC", accent="#24375E", accent_soft="#DEE4F1",
                               alert="#B4531E", alert_soft="#FBEADB", bar="#E3915C", edge="#EDB48F", dot="#BFC7D2"),
    "Forest & clay": dict(bg="#EDF1EE", surface="#FFFFFF", ink="#1E2620", muted="#4A544D", faint="#6C766E",
                          line="#DFE6E1", border="#C8D2CB", accent="#2F5D46", accent_soft="#DCE9E0",
                          alert="#A8462B", alert_soft="#F8E6DE", bar="#D77F5F", edge="#E8B19C", dot="#BDC8C0"),
    "Plum & sand": dict(bg="#F1EFF5", surface="#FFFFFF", ink="#251E2B", muted="#544B5A", faint="#756D7E",
                        line="#E5E1EC", border="#D0CADB", accent="#5A3E73", accent_soft="#ECE3F2",
                        alert="#B04A3A", alert_soft="#FAE7E2", bar="#DC8573", edge="#EDB6AA", dot="#C5BED1"),
}
PALETTE = "Midnight & apricot"
_P = PALETTES[PALETTE]
_AMBER, _AMBER_SOFT, _GREEN, _GREEN_SOFT = "#8A5A00", "#FBF3E0", "#3F6B4C", "#E6F0E8"


def root_vars(p: dict) -> str:
    return (":root { --bg:%(bg)s; --card:%(surface)s; --ink:%(ink)s; --ink2:%(muted)s; --mute:%(faint)s; --line:%(line)s; "
            "--border:%(border)s; --accent:%(accent)s; --accent-soft:%(accent_soft)s; --coral:%(alert)s; --coral-bg:%(alert_soft)s; "
            "--coral-bar:%(bar)s; --coral-edge:%(edge)s; --dot:%(dot)s; --stone:%(bg)s; "
            "--amber-bar:#D9A441; --amber:" + _AMBER + "; --amber-bg:" + _AMBER_SOFT + "; --green:" + _GREEN + "; --green-bg:" + _GREEN_SOFT + "; "
            '--serif:"Source Serif 4","Iowan Old Style",Georgia,serif; '
            '--sans:"Manrope",-apple-system,"Segoe UI",system-ui,sans-serif; }') % p


_CSS = """
<style>
/*ROOT*/
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {background:var(--bg); color:var(--ink);}
.stApp p, .stApp li, .stApp label, .stApp .sk-card, .sk-top, .sk-tile, .stApp input, .stApp textarea, .stApp button {font-family:var(--sans); font-weight:500;}
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li {font-size:15px; line-height:1.6; color:var(--ink);}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {color:var(--mute) !important;}
[data-testid="stMainBlockContainer"], .block-container {max-width:1120px; padding-top:1.4rem; padding-bottom:4rem;}
[data-testid="stSidebar"] {background:var(--card); border-right:1px solid var(--line);}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {color:var(--ink2);}
/* top bar */
.sk-top {display:flex; align-items:center; gap:.8rem; flex-wrap:wrap; padding:.1rem 0 1.2rem 0;}
.sk-logo {width:2.1rem; height:2.1rem; border-radius:.5rem; background:var(--accent); color:var(--card); font-family:var(--serif);
  font-weight:700; display:flex; align-items:center; justify-content:center; font-size:1.2rem;}
.sk-name {font-family:var(--serif); font-size:1.3rem; font-weight:700; line-height:1.1; color:var(--ink);}
.sk-tag {font-size:.8rem; color:var(--mute);}
.sk-badges {margin-left:auto; display:flex; gap:.45rem; flex-wrap:wrap;}
.sk-badge {font-size:.72rem; font-weight:700; letter-spacing:.04em; padding:.18rem .55rem; border-radius:3px; background:var(--stone); color:var(--ink2); border-left:3px solid var(--dot);}
.sk-badge.live, .sk-badge.mem {background:var(--green-bg); color:var(--green); border-left-color:var(--green);}
/* headings */
.sk-title {font-family:var(--serif); font-size:2.1rem; font-weight:600; line-height:1.2; margin:.2rem 0 .3rem 0; color:var(--ink);}
.sk-sub {color:var(--ink2); margin:0 0 1.4rem 0; max-width:46rem; line-height:1.6;}
.sk-section {font-family:var(--serif); font-size:1.3rem; font-weight:600; margin:1.8rem 0 .6rem 0; color:var(--ink);}
.sk-crumb {font-size:.85rem; color:var(--mute); margin-bottom:.4rem;}
.sk-crumb code {font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.8rem;}
.sk-h1 {font-family:var(--serif); font-size:2rem; font-weight:600; line-height:1.15; margin:.2rem 0 .8rem 0; color:var(--ink); max-width:48rem;}
.sk-lead {font-size:1.05rem; line-height:1.65; color:var(--ink2); max-width:50rem; margin:0 0 1.4rem 0;}
/* cards */
.sk-card {background:var(--card); border-radius:18px; padding:1.4rem 1.6rem; margin:0 0 1.4rem 0; box-shadow:0 1px 2px rgba(36,55,94,.07); color:var(--ink);}
.sk-card h2 {font-family:var(--serif); font-size:1.35rem; font-weight:600; margin:0 0 .9rem 0; color:var(--ink); line-height:1.25;}
.sk-card p {margin:.2rem 0 .6rem 0;}
.sk-note {font-size:.84rem; color:var(--mute); margin-top:.8rem; line-height:1.5;}
.sk-mono {font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.82rem;}
.sk-tiny {font-size:.74rem; color:var(--mute);}
/* summary tiles */
.sk-tiles {display:grid; grid-template-columns:repeat(3,1fr); gap:1.2rem; margin:0 0 1.6rem 0;}
@media (max-width: 900px) {.sk-tiles {grid-template-columns:1fr;}}
.sk-tile {background:var(--card); border-radius:16px; padding:1.1rem 1.3rem; box-shadow:0 1px 2px rgba(36,55,94,.07);}
.sk-tile small {display:block; font-size:.84rem; color:var(--ink2); margin-bottom:.3rem;}
.sk-tile b {display:block; font-family:var(--serif); font-size:1.7rem; font-weight:600; line-height:1.2; color:var(--ink);}
.sk-tile b.bad {color:var(--coral);} .sk-tile b.warn {color:var(--amber);} .sk-tile b.ok {color:var(--green);}
.sk-tile span {display:block; font-size:.84rem; color:var(--mute); margin-top:.3rem; line-height:1.45;}
/* score bar */
.sk-bar {position:relative; height:.5rem; border-radius:.3rem; background:var(--stone); margin:.75rem 0 1.1rem 0;}
.sk-bar > i {position:absolute; left:0; top:0; bottom:0; border-radius:.3rem;}
.sk-bar > u {position:absolute; top:-.28rem; bottom:-.28rem; width:2px; background:var(--ink2); opacity:.7;}
.sk-bar > em {position:absolute; top:.8rem; font-size:.7rem; font-style:normal; color:var(--mute); transform:translateX(-50%); white-space:nowrap;}
/* pills */
.sk-pill {display:inline-block; font-size:.8rem; font-weight:600; padding:.15rem .6rem; border-radius:6px; letter-spacing:.02em; white-space:nowrap;}
.sk-pill.ok {color:var(--green); background:var(--green-bg);} .sk-pill.warn {color:var(--amber); background:var(--amber-bg);}
.sk-pill.bad {color:var(--coral); background:var(--coral-bg);} .sk-pill.neutral {color:var(--ink2); background:var(--stone);}
/* soft callout */
.sk-callout {background:var(--coral-bg); color:var(--coral); padding:.9rem 1.1rem; border-radius:10px; font-size:1rem; line-height:1.55; margin:.2rem 0 1.2rem 0;}
.sk-callout.ok {background:var(--green-bg); color:var(--green);} .sk-callout.warn {background:var(--amber-bg); color:var(--amber);}
.sk-callout.plain {background:var(--stone); color:var(--ink);}
.sk-callout small {display:block; opacity:.8; margin-bottom:.15rem; font-size:.78rem;}
/* facts: 2-col definition list */
.sk-facts {display:grid; grid-template-columns:1fr 1fr; gap:1rem 2rem;}
@media (max-width: 800px) {.sk-facts {grid-template-columns:1fr;}}
.sk-facts div span {display:block; font-size:.82rem; color:var(--mute);}
.sk-facts div b {display:block; font-weight:600; font-size:.98rem; color:var(--ink); word-break:break-word;}
.sk-facts div i {display:block; font-style:normal; font-size:.72rem; color:var(--mute); margin-top:.1rem;}
.sk-facts code {font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.85rem; font-weight:500;}
/* two soft panels */
.sk-two {display:grid; grid-template-columns:1fr 1fr; gap:1rem;}
@media (max-width: 800px) {.sk-two {grid-template-columns:1fr;}}
.sk-panel {background:var(--stone); border-radius:10px; padding:1rem 1.1rem;}
.sk-panel small {display:block; color:var(--mute); font-size:.8rem;}
.sk-panel h3 {font-family:var(--serif); font-size:1.1rem; font-weight:600; margin:.15rem 0 .5rem 0; color:var(--ink);}
.sk-panel p {margin:.15rem 0; font-size:.92rem; color:var(--ink2);}
/* ownership chain */
.sk-chain {display:flex; align-items:center; flex-wrap:nowrap; gap:.4rem; margin:.2rem 0; overflow-x:auto;}
.sk-node {background:var(--stone); border-radius:10px; padding:.6rem .8rem; flex:1 1 0; min-width:0; text-align:center;}
.sk-node b {display:block; font-size:.95rem; color:var(--ink);} .sk-node span {font-size:.74rem; color:var(--mute);}
.sk-node.hot {background:var(--coral-bg); border-radius:999px; padding:.6rem 1rem; text-align:center;}
.sk-node.hot b {color:var(--coral);}
.sk-edge {display:flex; flex-direction:column; align-items:center; flex:0 0 4.2rem; padding:0 .1rem;}
.sk-edge em {font-style:normal; font-size:.76rem; color:var(--coral);}
.sk-edge i {display:block; width:100%; height:2px; background:var(--coral-edge); margin-top:.15rem;}
/* score rows */
.sk-rows {margin:.2rem 0;}
.sk-row {display:flex; justify-content:space-between; gap:1rem; padding:.7rem 0; border-bottom:1px solid var(--line);}
.sk-row:last-child {border-bottom:none;}
.sk-row b {display:block; font-weight:600; color:var(--ink);} .sk-row span {display:block; font-size:.84rem; color:var(--ink2);}
.sk-row .w {font-variant-numeric:tabular-nums; font-weight:600; color:var(--coral); white-space:nowrap;}
.sk-row.total .w, .sk-row.total b {color:var(--ink); font-size:1.05rem;}
/* timeline */
.sk-tl {list-style:none; margin:.2rem 0 0 0; padding:0 0 0 1.1rem; border-left:2px solid var(--line);}
.sk-tl li {position:relative; margin:0 0 1rem 0; padding-left:.5rem; line-height:1.45; color:var(--ink);}
.sk-tl li:before {content:""; position:absolute; left:-1.6rem; top:.4rem; width:.7rem; height:.7rem; border-radius:50%; background:var(--dot);}
.sk-tl li:last-child:before {background:var(--accent);}
.sk-tl li small {display:block; color:var(--mute); font-size:.78rem;}
/* chips */
.sk-chips {display:flex; flex-wrap:wrap; gap:.4rem; margin:.3rem 0 .6rem 0;}
.sk-chip {font-size:.78rem; padding:.2rem .6rem; border-radius:6px; background:var(--stone); color:var(--ink2);}
.sk-chip b {font-weight:600; margin-right:.3rem; color:var(--ink);}
/* html tables */
.sk-tablewrap {overflow-x:auto;}
.sk-table {width:100%; border-collapse:collapse; font-size:.9rem;}
.sk-table th {text-align:left; font-weight:600; font-size:.78rem; color:var(--mute); padding:.4rem .6rem; border-bottom:1px solid var(--line);}
.sk-table td {padding:.5rem .6rem; border-bottom:1px solid var(--line); vertical-align:top;}
.sk-code {font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.8rem; background:var(--stone); padding:.05rem .35rem; border-radius:.3rem;}
/* Streamlit widgets in the same language */
[data-testid="stMetric"] {background:var(--card); border-radius:16px; padding:1rem 1.2rem; box-shadow:0 1px 2px rgba(36,55,94,.07);}
[data-testid="stMetricValue"] {font-family:var(--serif); font-weight:600; font-size:1.6rem; font-variant-numeric:tabular-nums;}
[data-testid="stMetricValue"] > div {overflow:visible; text-overflow:clip; white-space:normal;}
[data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] div {white-space:normal; overflow:visible; text-overflow:clip; color:var(--ink2);}
[data-testid="stExpander"] {background:var(--card); border:none !important; border-radius:16px; box-shadow:0 1px 2px rgba(36,55,94,.07);}
[data-testid="stExpander"] summary {padding:1rem 1.4rem;}
[data-testid="stExpander"] summary p {font-family:var(--serif); font-size:1.1rem; font-weight:600; color:var(--ink);}
[data-testid="stExpanderDetails"] {padding:0 1.4rem 1.2rem 1.4rem;}
[data-testid="stForm"] {background:var(--card); border:none !important; border-radius:16px; padding:1.3rem 1.4rem; box-shadow:0 4px 18px rgba(36,55,94,.10);}
[data-testid="stColumn"]:has(.sk-sticky), [data-testid="column"]:has(.sk-sticky) {position:sticky; top:1rem; align-self:flex-start;}
.sk-form-note {font-size:.9rem; color:var(--ink2); margin:.1rem 0 .9rem 0;}
[data-testid="stAlert"], [data-testid="stAlertContainer"] {border-radius:14px;}
[data-testid="stForm"] [data-testid="stAlert"] {background:var(--coral-bg); color:var(--coral);}
button[kind="primary"], button[kind="primaryFormSubmit"] {background:var(--accent); border-color:var(--accent); color:var(--card); border-radius:12px;}
button[kind="secondary"], button[kind="secondaryFormSubmit"] {border-radius:12px;}
[data-testid="stDataFrame"] {border-radius:14px; overflow:hidden;}
.sk-report {font-family:var(--serif); background:var(--stone); border-radius:10px; padding:1.2rem 1.4rem; line-height:1.7; color:var(--ink);}
.sk-side-brand {font-family:var(--serif); font-size:1.3rem; font-weight:700; color:var(--ink);}
.sk-side-purpose {font-size:.82rem; color:var(--ink2); margin:.4rem 0 .9rem 0; line-height:1.45;}
.sk-banner {padding:.55rem .8rem; border-radius:8px; font-size:.84rem; font-weight:600; margin:.4rem 0;}
.sk-banner.ok {color:var(--green); background:var(--green-bg);} .sk-banner.bad {color:var(--coral); background:var(--coral-bg);}

/* v3: meta line, evidence block, section card, nav pills */
.sk-meta {font-size:.88rem; color:var(--mute); margin:-.6rem 0 1.4rem 0;}
.sk-card.multi {padding:0;} .sk-card.multi .sk-sec {padding:1.4rem 1.7rem; border-top:1px solid var(--line);}
.sk-card.multi .sk-sec:first-child {border-top:none;}
.sk-card.multi h2 {font-size:1.25rem; margin:0 0 .9rem 0;}
.sk-ev small {display:block; color:var(--ink2); font-size:.84rem;}
.sk-score {font-family:var(--serif); font-size:3.6rem; font-weight:600; line-height:1.05; font-variant-numeric:tabular-nums;}
.sk-score.bad {color:var(--coral);} .sk-score.warn {color:var(--amber);}
.sk-ev > span {display:block; font-size:.8rem; color:var(--mute); margin:0 0 1rem 0;}
.sk-h2 {font-family:var(--serif); font-size:1.4rem; font-weight:600; margin:.4rem 0 .6rem 0; color:var(--ink);}
.sk-h3 {font-family:var(--serif); font-size:1.1rem; font-weight:600; margin:1.2rem 0 .5rem 0; color:var(--ink);}
.sk-tick.ok {color:var(--green); font-weight:600;} .sk-tick.bad {color:var(--coral); font-weight:600;}
.sk-navrow {align-items:center;}
.sk-badges.right {justify-content:flex-end; margin-left:0;}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] {display:none !important;}
[data-testid="stRadio"] label[data-baseweb="radio"], [data-testid="stRadio"] label[data-testid="stRadioOption"] {background:transparent; padding:.3rem .7rem; border-radius:999px; margin:0; white-space:nowrap;}
[data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child, [data-testid="stRadio"] label[data-testid="stRadioOption"] > div > div:first-child {display:none !important;}
[data-testid="stRadio"] label[data-baseweb="radio"] p, [data-testid="stRadio"] label[data-testid="stRadioOption"] p {font-size:.88rem; color:var(--ink2);}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked), [data-testid="stRadio"] label[data-testid="stRadioOption"]:has(input:checked) {background:var(--card); box-shadow:0 1px 3px rgba(36,55,94,.16);}
[data-testid="stRadio"] label:has(input:checked) p {color:var(--ink); font-weight:700;}
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {background:var(--bg) !important; border-radius:12px !important;}
button[kind="primary"] p, button[kind="primaryFormSubmit"] p {color:var(--card) !important;}
[data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"] {background:var(--bg) !important; border:1px solid var(--border) !important; border-radius:12px !important;}
[data-testid="stTextInputRootElement"] input, [data-testid="stTextAreaRootElement"] textarea {background:transparent !important; color:var(--ink) !important;}

/* v5 taste pass: numerals, tracking, balance, states, texture */
body {font-variant-numeric:tabular-nums;}
.sk-title, .sk-h1, .sk-section, .sk-h2, .sk-h3, .sk-card h2, .sk-panel h3, [data-testid="stExpander"] summary p {text-wrap:balance;}
.sk-title, .sk-h1 {letter-spacing:-.02em;}
.sk-score {letter-spacing:-.03em;}
.sk-section, .sk-h2, .sk-card h2, .sk-tile b {letter-spacing:-.01em;}
.sk-tile small, .sk-facts div span, .sk-sec small, .sk-panel small, .sk-table th, .sk-crumb, .sk-meta {letter-spacing:.02em;}
.sk-table th {letter-spacing:.04em;}
.sk-table td, .sk-tile b, .sk-facts div b, .sk-row .w, .sk-chip, .sk-pill, .sk-meta, .sk-crumb, .sk-edge em,
[data-testid="stDataFrame"], [data-testid="stMetricValue"], [data-testid="stRadio"] label p {font-variant-numeric:tabular-nums lining-nums;}
.sk-sub, .sk-lead, .sk-note {text-wrap:pretty;}
/* one light direction (from above), navy-tinted */
.sk-card, .sk-tile, [data-testid="stMetric"], [data-testid="stExpander"] {transition:box-shadow .2s ease, transform .2s ease;}
.sk-tile:hover, [data-testid="stMetric"]:hover {box-shadow:0 2px 8px rgba(36,55,94,.10);}
/* grain: pure CSS, very light, never intercepts input */
.stApp::before {content:""; position:fixed; inset:0; z-index:0; pointer-events:none; opacity:.035;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}
/* buttons, nav pills, selects: hover / pressed / focus */
button[kind], [data-testid="stRadio"] label, [data-baseweb="select"] > div, [data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"], [data-testid="stExpander"] summary {transition:background-color .2s ease, box-shadow .2s ease, transform .2s ease, border-color .2s ease, color .2s ease;}
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover {background:#1B2C4D; border-color:#1B2C4D; box-shadow:0 2px 8px rgba(36,55,94,.28);}
button[kind="secondary"]:hover, button[kind="secondaryFormSubmit"]:hover {border-color:var(--accent); color:var(--accent); background:var(--accent-soft);}
button[kind]:active {transform:translateY(1px) scale(.98); box-shadow:none;}
button[kind]:focus-visible, [data-baseweb="select"]:focus-within > div, [data-testid="stExpander"] summary:focus-visible {outline:2px solid var(--accent) !important; outline-offset:2px; box-shadow:0 0 0 4px var(--accent-soft) !important;}
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextAreaRootElement"]:focus-within {border-color:var(--accent) !important; box-shadow:0 0 0 3px var(--accent-soft);}
[data-testid="stRadio"] label:hover {background:rgba(36,55,94,.07);}
[data-testid="stRadio"] label:has(input:checked):hover {background:var(--card);}
[data-testid="stRadio"] label:active {transform:translateY(1px) scale(.98);}
[data-testid="stRadio"] label:has(input:focus-visible) {outline:2px solid var(--accent); outline-offset:2px;}
[data-testid="stRadio"] label:has(input:checked) {box-shadow:inset 0 -2px 0 var(--accent), 0 1px 3px rgba(36,55,94,.16);}
[data-testid="stExpander"] summary:hover {background:rgba(36,55,94,.04); border-radius:16px;}
/* skeleton-style loading instead of a spinner */
[data-testid="stSpinner"] {min-height:2.4rem; border-radius:10px; padding:.6rem 1rem; color:var(--mute);
  background:linear-gradient(90deg,var(--line) 25%,var(--accent-soft) 50%,var(--line) 75%); background-size:200% 100%; animation:sk-shimmer 1.4s ease-in-out infinite;}
[data-testid="stSpinner"] svg, [data-testid="stSpinner"] i {opacity:0;}
@keyframes sk-shimmer {0% {background-position:200% 0;} 100% {background-position:-200% 0;}}
/* composed empty / inline error states */
[data-testid="stAlert"] {border:none; box-shadow:inset 3px 0 0 currentColor;}
[data-testid="stAlert"] p {font-size:.95rem;}
@media (prefers-reduced-motion: reduce) {* {transition:none !important; animation:none !important;}}
</style>
"""
CSS = _CSS.replace("/*ROOT*/", root_vars(_P))
_TONE_STYLE.update({"ok": (_GREEN, _GREEN_SOFT), "warn": (_AMBER, _AMBER_SOFT), "bad": (_P["alert"], _P["alert_soft"]),
                    "neutral": (_P["muted"], _P["line"])})



def esc(x) -> str:
    return html.escape(str(x), quote=True)


# ----------------------------------------------------------------------------- formatters
def fmt_money(amount, currency: str = "") -> str:
    """'USD 3,808,600' (currency first, no decimals)."""
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return "-"
    if a != a:  # NaN
        return "-"
    return f"{currency} {a:,.0f}".strip()


def fmt_compact(amount, currency: str = "") -> str:
    """Short money for cards and tables: 'USD 3.8M'."""
    try:
        a = float(amount or 0)
    except (TypeError, ValueError):
        return "-"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(a) >= div:
            return f"{currency} {a / div:,.1f}{suf}".strip()
    return f"{currency} {a:,.0f}".strip()


def status_label(status: str) -> str:
    return STATUS_META.get(status, (str(status), "neutral"))[0]


def status_pill(status: str, pending_label: str | None = None) -> str:
    label, tone = STATUS_META.get(status, (str(status), "neutral"))
    if pending_label and status == "PENDING_APPROVAL":
        label = pending_label
    return f'<span class="sk-pill {tone}">{esc(label)}</span>'


def status_cell_css(display_label: str) -> str:
    """Styler CSS for a status cell whose text is a display label (see STATUS_META)."""
    for label, tone in STATUS_META.values():
        if label == display_label:
            fg, bg = _TONE_STYLE[tone]
            return f"background-color: {bg}; color: {fg}; font-weight: 600"
    return ""


def style_status(df: pd.DataFrame, col: str = "Status"):
    """df -> pandas Styler colouring `col` (display labels); falls back to the plain frame on any problem."""
    try:
        sty = df.style
        fn = getattr(sty, "map", None) or sty.applymap  # pandas >=2.1 renamed applymap -> map
        return fn(status_cell_css, subset=[col])
    except Exception:
        return df


def bank_label(bank_id) -> str:
    """'BANK_A' -> 'Bank A'; unknown ids pass through."""
    b = str(bank_id or "?")
    return f"Bank {b[5:]}" if b.upper().startswith("BANK_") else b


def strength(score, threshold: float) -> tuple[str, str]:
    """(word, tone) for the evidence-strength tile."""
    if score is None:
        return "Not scored", "warn"
    s = float(score)
    if s >= 0.80:
        return "Strong", "bad"
    if s >= round(float(threshold), 4):
        return "Moderate", "bad"
    return "Weak", "warn"


# ----------------------------------------------------------------------------- html parts
def brand_header(backend: str, live: bool) -> str:
    badge = ('<span class="sk-badge live">Snowflake live</span>' if live
             else '<span class="sk-badge mem">Memory backend</span>')
    return ('<div class="sk-top"><div class="sk-logo">S</div><div>'
            '<div class="sk-name">Suraksha</div>'
            '<div class="sk-tag">Duplicate trade-finance detection · glass-box, human-approved</div></div>'
            f'<div class="sk-badges"><span class="sk-badge">Synthetic data only</span>{badge}</div></div>')


def brand_mark() -> str:
    return '<div class="sk-top" style="padding:0"><div class="sk-logo">S</div><div class="sk-name">Suraksha</div></div>'


def badges(live: bool) -> str:
    badge = ('<span class="sk-badge live">Snowflake live</span>' if live
             else '<span class="sk-badge mem">Memory backend</span>')
    return f'<div class="sk-badges right"><span class="sk-badge">Synthetic data only</span>{badge}</div>'


def page_header(title: str, sub: str) -> str:
    s = f'<div class="sk-sub">{esc(sub)}</div>' if sub else ""
    return f'<div class="sk-title">{esc(title)}</div>{s}'


def section(title: str) -> str:
    return f'<div class="sk-section">{esc(title)}</div>'


def pill_html(text: str, tone: str = "neutral") -> str:
    return f'<span class="sk-pill {tone}">{esc(text)}</span>'


def banner(text: str, tone: str = "ok") -> str:
    return f'<div class="sk-banner {tone}">{esc(text)}</div>'


def callout(text: str, tone: str = "", label: str = "") -> str:
    lab = f"<small>{esc(label)}</small>" if label else ""
    return f'<div class="sk-callout {tone}">{lab}{esc(text)}</div>'


def sections_card(sections: list[tuple[str, str]]) -> str:
    """ONE white card, sections separated by hairlines. (title, body_html) pairs."""
    parts = "".join(f'<div class="sk-sec"><h2>{esc(t)}</h2>{b}</div>' for t, b in sections)
    return f'<div class="sk-card multi">{parts}</div>'


FONT_CSS = ("<style>@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700"
            "&family=Source+Serif+4:opsz,wght@8..60,600&display=swap');</style>")


def card(title: str, body_html: str, note: str = "") -> str:
    """White card with a serif heading. body_html must already be escaped/built by this module."""
    n = f'<div class="sk-note">{esc(note)}</div>' if note else ""
    h = f"<h2>{esc(title)}</h2>" if title else ""
    return f'<div class="sk-card">{h}{body_html}{n}</div>'


def breadcrumb(text: str, code: str = "") -> str:
    c = f' · <code>{esc(code)}</code>' if code else ""
    return f'<div class="sk-crumb">{esc(text)}{c}</div>'


def hero(h1: str, lead: str) -> str:
    return f'<div class="sk-h1">{esc(h1)}</div>' + (f'<div class="sk-lead">{esc(lead)}</div>' if lead else "")


def score_bar(score: float | None, threshold: float) -> str:
    """Thin score bar with a threshold tick. Coral at/above the threshold, amber below."""
    s = 0.0 if score is None else max(0.0, min(1.0, float(score)))
    t = max(0.0, min(1.0, float(threshold)))
    colour = "var(--coral-bar)" if s >= round(t, 4) else "var(--amber-bar)"
    return (f'<div class="sk-bar" title="score {s:.2f} vs threshold {t:.2f}"><i style="width:{s * 100:.1f}%;background:{colour}"></i>'
            f'<u style="left:{t * 100:.1f}%"></u><em style="left:{t * 100:.1f}%">{t:.2f}</em></div>')


def tile(label: str, big: str, sub: str, tone: str = "", extra_html: str = "") -> str:
    return f'<div class="sk-tile"><small>{esc(label)}</small><b class="{tone}">{esc(big)}</b>{extra_html}<span>{esc(sub)}</span></div>'


def tiles(*items: str) -> str:
    return f'<div class="sk-tiles">{"".join(items)}</div>'


def facts(items: list[tuple]) -> str:
    """(label, value[, tiny grey source[, mono]]) -> two-column definition list; empty values are skipped."""
    cells = []
    for it in items:
        label, value = it[0], it[1]
        tiny = it[2] if len(it) > 2 else ""
        mono = it[3] if len(it) > 3 else False
        if value in (None, ""):
            continue
        v = f"<code>{esc(value)}</code>" if mono else esc(value)
        t = f"<i>{esc(tiny)}</i>" if tiny else ""
        cells.append(f"<div><span>{esc(label)}</span><b>{v}</b>{t}</div>")
    return f'<div class="sk-facts">{"".join(cells)}</div>'


def panel(kicker: str, title: str, lines: list[str]) -> str:
    ps = "".join(f"<p>{esc(x)}</p>" for x in lines)
    return f'<div class="sk-panel"><small>{esc(kicker)}</small><h3>{esc(title)}</h3>{ps}</div>'


def two(left: str, right: str) -> str:
    return f'<div class="sk-two">{left}{right}</div>'


def node_chain(cards: list[dict], relations: list[str]) -> str:
    """Borrower -> (relation) -> shared person/company (pill) -> (relation) -> borrower."""
    parts = []
    for i, c in enumerate(cards):
        sub = f'<span>{esc(c.get("sub", ""))}</span>' if c.get("sub") else ""
        parts.append(f'<div class="sk-node{" hot" if c.get("hot") else ""}"><b>{esc(c["label"])}</b>{sub}</div>')
        if i < len(relations):
            parts.append(f'<div class="sk-edge"><em>{esc(relations[i])}</em><i></i></div>')
    return f'<div class="sk-chain">{"".join(parts)}</div>'


def score_rows(rows: list[tuple], total: str) -> str:
    """(label, detail, weight_text) rows plus a Total row. Details are escaped here."""
    body = "".join(f'<div class="sk-row"><div><b>{esc(l)}</b><span>{esc(d)}</span></div><div class="w">{esc(w)}</div></div>'
                   for l, d, w in rows)
    body += f'<div class="sk-row total"><div><b>Total</b></div><div class="w">{esc(total)}</div></div>'
    return f'<div class="sk-rows">{body}</div>'


def timeline(items: list[tuple[str, str]]) -> str:
    """(plain sentence, 'by the system · 12:04') -> gentle timeline."""
    li = "".join(f"<li>{esc(s)}<small>{esc(w)}</small></li>" for s, w in items)
    return f'<ul class="sk-tl">{li}</ul>'


def html_table(headers: list[str], rows: list[list], mono: set[int] | None = None) -> str:
    mono = mono or set()
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for r in rows:
        cells = [f'<td><code class="sk-code">{esc(v)}</code></td>' if i in mono else f"<td>{esc(v)}</td>"
                 for i, v in enumerate(r)]
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<div class="sk-tablewrap"><table class="sk-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def citation_chips(cits) -> str:
    """Compact chips from Citation objects (kind / ref / page / snippet); snippet goes in the tooltip."""
    if not cits:
        return ""
    chips = []
    for c in cits:
        kind = getattr(getattr(c, "kind", None), "value", getattr(c, "kind", ""))
        page = f" p.{c.page}" if getattr(c, "page", None) else ""
        tip = esc(getattr(c, "snippet", "") or "")
        chips.append(f'<span class="sk-chip" title="{tip}"><b>{esc(kind)}</b>{esc(c.ref)}{esc(page)}</span>')
    return f'<div class="sk-chips">{"".join(chips)}</div>'


def calm(text, vessel=False):
    """Document values often arrive in capitals ("GRANULAR UREA", "M/V EVERGREEN TRITON"); show them calmly."""
    t = str(text or "").strip()
    if vessel:
        import re as _re
        t = _re.sub(r"^(M\s*/\s*V|MV|M\.V\.)\s+", "", t, flags=_re.I)
    return t.title() if t.isupper() else t
