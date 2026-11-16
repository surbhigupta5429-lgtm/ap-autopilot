"""Presentation helpers: CSS, pipeline track, stage cards, banners, tiles."""
import html
import io

import streamlit as st

from fmt import inr

# Each stage has its own accent colour + icon, so the pipeline reads at a glance.
STAGE_META = {
    "ingest": ("Receive", "📥", "#6366f1"),
    "extract": ("Extract", "🔍", "#8b5cf6"),
    "validate": ("Validate", "🧮", "#06b6d4"),
    "vendor": ("Vendor", "🏢", "#14b8a6"),
    "duplicate": ("Duplicates", "👯", "#f97316"),
    "po": ("PO match", "🔗", "#3b82f6"),
    "amounts": ("Price & balance", "⚖️", "#ec4899"),
    "decide": ("Decision", "🏁", "#10b981"),
}
STATUS = {  # icon, colour, soft background, label
    "pending": ("", "#94a3b8", "#f1f5f9", "Waiting"),
    "running": ("", "#6366f1", "#eef2ff", "Running"),
    "pass": ("✓", "#16a34a", "#dcfce7", "Passed"),
    "warn": ("!", "#d97706", "#fef3c7", "Attention"),
    "fail": ("✕", "#dc2626", "#fee2e2", "Failed"),
    "skip": ("–", "#64748b", "#e2e8f0", "Skipped"),
}
DECISION = {
    "APPROVE": ("Approved for payment", "linear-gradient(135deg,#059669,#10b981)", "✓"),
    "REVIEW": ("Needs human review", "linear-gradient(135deg,#d97706,#f59e0b)", "!"),
    "REJECT": ("Rejected", "linear-gradient(135deg,#dc2626,#f43f5e)", "✕"),
}
STATUS_LABEL = {
    "approved": ("Auto-approved", "#15803d", "#dcfce7"),
    "approved_by_reviewer": ("Approved by reviewer", "#15803d", "#dcfce7"),
    "pending_review": ("Pending review", "#b45309", "#fef3c7"),
    "rejected": ("Rejected", "#b91c1c", "#fee2e2"),
    "rejected_by_reviewer": ("Rejected by reviewer", "#b91c1c", "#fee2e2"),
}
SEVERITY = {"block": ("Blocking", "#b91c1c", "#fee2e2"), "review": ("Review", "#b45309", "#fef3c7"),
            "info": ("Info", "#475569", "#e2e8f0")}

# Test-invoice tiles: gradient per scenario.
TILE_STYLE = ["#6366f1,#8b5cf6", "#0ea5e9,#06b6d4", "#14b8a6,#10b981", "#f59e0b,#f97316",
              "#ec4899,#f43f5e", "#8b5cf6,#d946ef", "#3b82f6,#6366f1", "#64748b,#475569", "#dc2626,#9333ea"]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, button, input { font-family: 'Inter', system-ui, sans-serif !important; }
.block-container { padding-top: 2.4rem; max-width: 1320px; }
h1, h2, h3 { letter-spacing: -0.02em; }

/* ---------- Sidebar ---------- */
section[data-testid="stSidebar"] { width: 310px !important; min-width: 310px !important;
  background: linear-gradient(180deg, #eef2ff 0%, #f5f3ff 45%, #fdf4ff 100%); border-right: 1px solid #e0e7ff; }
.brand { display: flex; gap: 12px; align-items: center; margin: 4px 0 22px 0; }
.brand-logo { width: 46px; height: 46px; border-radius: 14px; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #6366f1, #a855f7); color: #fff; font-size: 1.45rem;
  box-shadow: 0 6px 16px rgba(99,102,241,.35); }
.brand-name { font-weight: 800; font-size: 1.25rem; color: #1e1b4b; line-height: 1.1; }
.brand-tag { font-size: .8rem; color: #6b7280; margin-top: 2px; }
.side-label { font-size: .72rem; font-weight: 700; letter-spacing: .1em; color: #6366f1; text-transform: uppercase;
  margin: 18px 0 8px 2px; }
[class*="st-key-nav_"] button { height: 58px; border-radius: 14px !important; justify-content: flex-start !important;
  padding-left: 18px !important; border: 1px solid #e0e7ff !important;
  background: #ffffff !important; color: #312e81 !important; box-shadow: 0 1px 2px rgba(30,27,75,.05);
  transition: all .15s ease; }
[class*="st-key-nav_"] button:hover { border-color: #a5b4fc !important; transform: translateX(2px);
  box-shadow: 0 4px 12px rgba(99,102,241,.15); }
[class*="st-key-nav_"] button[kind="primary"] { background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
  color: #fff !important; border: none !important; box-shadow: 0 6px 18px rgba(99,102,241,.35); }
[class*="st-key-nav_"] button p { font-size: 1rem !important; font-weight: 600 !important; }
[class*="st-key-nav_"] button > div { justify-content: flex-start !important; }
.ai-pill { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: 12px; font-size: .85rem;
  font-weight: 600; margin-top: 10px; }
.ai-pill .dotp { width: 9px; height: 9px; border-radius: 50%; }
.st-key-reset_btn button { border-radius: 12px !important; border: 1px dashed #a5b4fc !important; background: #fff !important;
  color: #4338ca !important; }
.st-key-reset_btn button p { font-weight: 600 !important; }

/* ---------- Hero / upload (iLovePDF-style) ---------- */
.hero { text-align: center; padding: 6px 0 4px 0; }
.hero-title { font-size: 2.6rem; font-weight: 800; color: #1e1b4b; letter-spacing: -0.03em; }
.hero-title span { background: linear-gradient(135deg, #6366f1, #a855f7, #ec4899); -webkit-background-clip: text;
  background-clip: text; color: transparent; }
.hero-sub { font-size: 1.12rem; color: #4b5563; margin: 8px auto 22px auto; max-width: 720px; }
[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"] {
  min-height: 190px; border: 2px dashed #a5b4fc; border-radius: 22px;
  background: radial-gradient(circle at 50% 0%, #eef2ff 0%, #ffffff 70%);
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; padding: 28px; }
[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"]:hover { border-color: #6366f1; background: #eef2ff; }
[data-testid="stFileUploaderDropzone"] button {
  background: linear-gradient(135deg, #6366f1, #8b5cf6) !important; color: #fff !important; border: none !important;
  padding: 16px 46px !important; border-radius: 14px !important;
  box-shadow: 0 10px 24px rgba(99,102,241,.35); min-height: 62px; }
[data-testid="stFileUploaderDropzone"] button, [data-testid="stFileUploaderDropzone"] button * {
  font-size: 1.2rem !important; font-weight: 700 !important; color: #fff !important; }
[data-testid="stFileUploaderDropzone"] button:hover { filter: brightness(1.07); }
[data-testid="stFileUploaderDropzoneInstructions"] { justify-content: center; text-align: center; }
[data-testid="stFileUploaderDropzoneInstructions"] span { font-size: .95rem; color: #6b7280; }
.or-line { display: flex; align-items: center; gap: 14px; color: #9ca3af; font-size: .82rem; font-weight: 700;
  letter-spacing: .1em; margin: 22px 0 12px 0; text-transform: uppercase; }
.or-line::before, .or-line::after { content: ""; flex: 1; height: 1px; background: #e5e7eb; }
[class*="st-key-tile_"] button { height: 74px; border-radius: 16px !important; border: none !important;
  box-shadow: 0 6px 16px rgba(15,23,42,.12); transition: all .15s ease; }
[class*="st-key-tile_"] button p { font-size: .92rem !important; font-weight: 700 !important; color: #fff !important; }
[class*="st-key-tile_"] button:hover { transform: translateY(-2px); box-shadow: 0 10px 22px rgba(15,23,42,.18); }
.filechip { display: flex; align-items: center; gap: 12px; padding: 14px 18px; border-radius: 16px; background: #f5f3ff;
  border: 1px solid #ddd6fe; margin: 4px 0 10px 0; }
.filechip .ic { font-size: 1.6rem; }
.filechip .nm { font-weight: 700; color: #1e1b4b; }
.filechip .sz { color: #6b7280; font-size: .85rem; }
.st-key-run_btn button { height: 56px; border-radius: 14px !important; border: none !important;
  background: linear-gradient(135deg, #6366f1, #8b5cf6) !important; box-shadow: 0 8px 20px rgba(99,102,241,.35); }
.st-key-run_btn button p { font-size: 1.1rem !important; font-weight: 700 !important; color: #fff !important; }

/* ---------- Section headers ---------- */
.sec { display: flex; align-items: center; gap: 10px; margin: 30px 0 14px 0; }
.sec .n { width: 32px; height: 32px; border-radius: 10px; display: flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 800; font-size: .95rem; background: linear-gradient(135deg, #6366f1, #a855f7); }
.sec .t { font-size: 1.4rem; font-weight: 800; color: #1e1b4b; }
.sec .s { color: #6b7280; font-size: .92rem; margin-left: 6px; }

/* ---------- Pipeline track ---------- */
.track { display: grid; grid-template-columns: repeat(8, 1fr); background: #fff; border: 1px solid #e5e7eb;
  border-radius: 20px; padding: 22px 10px 16px 10px; box-shadow: 0 4px 14px rgba(15,23,42,.05); margin-bottom: 18px; }
.node { position: relative; text-align: center; }
.node::before { content: ""; position: absolute; top: 26px; left: -50%; width: 100%; height: 4px; background: #e5e7eb;
  z-index: 0; border-radius: 2px; }
.node:first-child::before { display: none; }
.node.done::before { background: linear-gradient(90deg, #a5b4fc, #c4b5fd); }
.node .dot { position: relative; z-index: 1; width: 54px; height: 54px; margin: 0 auto; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; font-size: 1.5rem; background: #f8fafc;
  border: 3px solid #e2e8f0; transition: all .2s; }
.node.pending .dot { filter: grayscale(1); opacity: .5; }
.node.running .dot { background: #eef2ff; border-color: var(--a); animation: pulse 1s ease-in-out infinite; }
.node.pass .dot { background: color-mix(in srgb, var(--a) 14%, white); border-color: #22c55e; }
.node.warn .dot { background: #fffbeb; border-color: #f59e0b; }
.node.fail .dot { background: #fef2f2; border-color: #ef4444; }
.node.skip .dot { filter: grayscale(1); opacity: .6; border-style: dashed; }
.node .badge { position: absolute; right: -5px; bottom: -5px; width: 22px; height: 22px; border-radius: 50%; color: #fff;
  font-size: .72rem; font-weight: 800; display: flex; align-items: center; justify-content: center; border: 2px solid #fff; }
.node .lbl { font-size: .8rem; font-weight: 700; color: #334155; margin-top: 9px; }
.node.pending .lbl { color: #94a3b8; font-weight: 600; }
@keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(99,102,241,.45); } 50% { box-shadow: 0 0 0 11px rgba(99,102,241,0); } }

/* ---------- Stage cards ---------- */
.scard { background: #fff; border: 1px solid #e5e7eb; border-top: 5px solid var(--a); border-radius: 16px;
  padding: 14px 16px; margin-bottom: 14px; min-height: 104px; box-shadow: 0 3px 10px rgba(15,23,42,.04); transition: all .2s; }
.scard.pending { opacity: .6; border-top-color: #cbd5e1; }
.scard.running { box-shadow: 0 0 0 3px #c7d2fe, 0 6px 18px rgba(99,102,241,.2); }
.scard.warn { background: linear-gradient(180deg, #fffbeb 0%, #fff 60%); }
.scard.fail { background: linear-gradient(180deg, #fef2f2 0%, #fff 60%); }
.shead { display: flex; align-items: center; gap: 12px; }
.sicon { width: 42px; height: 42px; border-radius: 12px; display: flex; align-items: center; justify-content: center;
  font-size: 1.25rem; background: color-mix(in srgb, var(--a) 16%, white); flex-shrink: 0; }
.scard.pending .sicon { filter: grayscale(1); }
.snum { font-size: .68rem; font-weight: 800; letter-spacing: .1em; color: var(--a); text-transform: uppercase; }
.stitle { font-weight: 700; color: #0f172a; font-size: 1rem; }
.sbadge { margin-left: auto; padding: 5px 11px; border-radius: 999px; font-size: .75rem; font-weight: 700; white-space: nowrap;
  color: var(--s); background: var(--sb); }
.sms { font-size: .7rem; color: #94a3b8; margin-left: 8px; font-weight: 600; letter-spacing: 0; text-transform: none; }
.ssum { color: #334155; font-size: .9rem; margin: 10px 0 0 54px; }
.scard details { margin: 8px 0 0 54px; }
.scard summary { cursor: pointer; color: var(--a); font-size: .8rem; font-weight: 700; }
.kv { display: grid; grid-template-columns: minmax(120px, 34%) 1fr; gap: 4px 12px; font-size: .8rem; margin-top: 8px; }
.kv .k { color: #64748b; } .kv .v { color: #0f172a; word-break: break-word; }
.spinner { width: 14px; height: 14px; border: 2px solid #c7d2fe; border-top-color: #6366f1; border-radius: 50%;
  animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ---------- Decision + findings ---------- */
.decision { border-radius: 20px; padding: 22px 26px; color: #fff; margin: 4px 0 16px 0; box-shadow: 0 10px 28px rgba(15,23,42,.15); }
.decision .label { font-size: .8rem; text-transform: uppercase; letter-spacing: .12em; font-weight: 800; opacity: .95; }
.decision .big { font-size: 1.45rem; font-weight: 800; margin-top: 6px; line-height: 1.35; }
.decision .meta { font-size: .9rem; margin-top: 10px; opacity: .92; }
.chip { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .74rem; font-weight: 700;
  color: var(--c); background: var(--bg); white-space: nowrap; }
.finding { border: 1px solid #e5e7eb; border-left: 5px solid var(--c); border-radius: 12px; padding: 12px 16px;
  margin-bottom: 10px; background: #fff; }
.finding .msg { font-weight: 600; color: #0f172a; font-size: .92rem; margin-top: 6px; }
.finding .act { color: #475569; font-size: .85rem; margin-top: 4px; }

/* ---------- Dashboard ---------- */
.kpi { border-radius: 18px; padding: 16px 18px; color: #fff; box-shadow: 0 8px 20px rgba(15,23,42,.12); min-height: 112px; }
.kpi .l { font-size: .8rem; font-weight: 600; opacity: .95; }
.kpi .n { font-size: 1.8rem; font-weight: 800; margin-top: 4px; font-variant-numeric: tabular-nums; }
.kpi .s { font-size: .76rem; opacity: .9; }
.conf { height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden; width: 70px; display: inline-block;
  vertical-align: middle; margin-right: 6px; }
.conf > span { display: block; height: 100%; }
table.fields { width: 100%; border-collapse: collapse; font-size: .86rem; }
table.fields td { padding: 7px 4px; border-bottom: 1px solid #f1f5f9; }
table.fields td.k { color: #64748b; width: 34%; }
.emaildraft { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: .8rem; background: #f8fafc;
  border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; color: #1e293b; }
.page-title { font-size: 2rem; font-weight: 800; color: #1e1b4b; letter-spacing: -0.02em; }
.page-sub { color: #6b7280; margin: 4px 0 20px 0; font-size: 1rem; }
/* ---------- Business impact ---------- */
.impact { border-radius: 22px; padding: 22px 24px; color: #fff; margin: 0 0 22px 0;
  background: radial-gradient(circle at 0% 0%, #7c3aed 0%, #4f46e5 45%, #1e1b4b 100%);
  box-shadow: 0 14px 34px rgba(49,46,129,.35); }
.impact-kicker { font-size: .74rem; font-weight: 800; letter-spacing: .14em; opacity: .85; }
.impact-big { font-size: 1.75rem; font-weight: 800; margin-top: 6px; letter-spacing: -0.02em; line-height: 1.25; }
.impact-big b { background: linear-gradient(90deg, #fde68a, #fbcfe8); -webkit-background-clip: text; background-clip: text;
  color: transparent; }
.impact-sub { opacity: .88; margin-top: 4px; font-size: .95rem; }
.impact-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 16px; }
.itile { background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.18); border-radius: 16px; padding: 14px; }
.itile .ii { font-size: 1.4rem; }
.itile .iv { font-size: 1.35rem; font-weight: 800; margin-top: 4px; font-variant-numeric: tabular-nums; }
.itile .il { font-size: .8rem; font-weight: 700; margin-top: 2px; }
.itile .is { font-size: .74rem; opacity: .8; margin-top: 2px; }
.impact-note { font-size: .74rem; opacity: .7; margin-top: 12px; }

/* ---------- Batch results ---------- */
table.batch { width: 100%; border-collapse: separate; border-spacing: 0 6px; font-size: .88rem; }
table.batch td { background: #fff; padding: 10px 12px; border-top: 1px solid #eef2ff; border-bottom: 1px solid #eef2ff; }
table.batch td:first-child { border-left: 5px solid var(--c); border-radius: 12px 0 0 12px; font-weight: 800; color: #6366f1; }
table.batch td:last-child { border-right: 1px solid #eef2ff; border-radius: 0 12px 12px 0; color: #475569; }
table.batch .nm { font-weight: 700; color: #0f172a; }
.st-key-run_all button { height: 54px; border-radius: 14px !important; border: 2px solid #c4b5fd !important;
  background: linear-gradient(135deg, #f5f3ff, #fdf4ff) !important; }
.st-key-run_all button p { font-size: 1.02rem !important; font-weight: 700 !important; color: #5b21b6 !important; }
</style>
"""


def e(x) -> str:
    return html.escape(str(x)) if x is not None else "—"


def chip(text, color, bg):
    return f'<span class="chip" style="--c:{color};--bg:{bg}">{e(text)}</span>'


def status_chip(status):
    label, c, bg = STATUS_LABEL.get(status, (status, "#475569", "#e2e8f0"))
    return chip(label, c, bg)


def section(num, title, sub=""):
    return (f'<div class="sec"><div class="n">{num}</div><div class="t">{e(title)}</div>'
            f'<div class="s">{e(sub)}</div></div>')


def track(states: dict) -> str:
    """Horizontal pipeline: one node per stage, coloured by its current status."""
    nodes = []
    for key, (label, icon, accent) in STAGE_META.items():
        st_ = states.get(key, "pending")
        sym, sc, _, _ = STATUS[st_]
        badge = f'<div class="badge" style="background:{sc}">{sym}</div>' if sym else ""
        done = " done" if st_ != "pending" else ""
        nodes.append(f'<div class="node {st_}{done}" style="--a:{accent}"><div class="dot">{icon}{badge}</div>'
                     f'<div class="lbl">{e(label)}</div></div>')
    return f'<div class="track">{"".join(nodes)}</div>'


def stage_card(idx: int, key: str, title: str, state: str = "pending", stage: dict | None = None) -> str:
    _, icon, accent = STAGE_META[key]
    sym, sc, sbg, slabel = STATUS[state]
    if state == "running":
        badge = ('<div class="sbadge" style="--s:#4f46e5;--sb:#eef2ff;display:flex;gap:6px;align-items:center">'
                 '<div class="spinner"></div>Running</div>')
    else:
        badge = f'<div class="sbadge" style="--s:{sc};--sb:{sbg}">{sym + " " if sym else ""}{slabel}</div>'
    ms = f'<span class="sms">{stage["ms"]} ms</span>' if stage else ""
    if stage:
        body = f'<div class="ssum">{e(stage["summary"])}</div>'
        rows = "".join(f'<div class="k">{e(k)}</div><div class="v">{e(v)}</div>' for k, v in stage["details"])
        for f in stage.get("findings", []):
            lab, fc, fbg = SEVERITY[f["severity"]]
            rows += f'<div class="k">{chip(lab, fc, fbg)}</div><div class="v">{e(f["message"])}</div>'
        if rows:
            opened = "open" if state in ("warn", "fail") else ""
            body += f'<details {opened}><summary>View evidence</summary><div class="kv">{rows}</div></details>'
    elif state == "running":
        body = '<div class="ssum" style="color:#6366f1">Working on it…</div>'
    else:
        body = '<div class="ssum" style="color:#94a3b8">Waiting to start</div>'
    return (f'<div class="scard {state}" style="--a:{accent}"><div class="shead"><div class="sicon">{icon}</div>'
            f'<div><div class="snum">Step {idx:02d}{ms}</div><div class="stitle">{e(title)}</div></div>{badge}</div>'
            f'{body}</div>')


def decision_banner(run: dict) -> str:
    label, grad, icon = DECISION[run["decision"]]
    headline = run["headline"].split(" — ", 1)[-1]
    meta = (f'{e(run.get("vendor_name"))} · Invoice {e(run.get("invoice_number"))} · '
            f'PO {e(run.get("po_number"))} · Total {inr(run.get("total"), 2)} · {run["duration_ms"]} ms')
    return (f'<div class="decision" style="background:{grad}"><div class="label">{icon}&nbsp; {label}</div>'
            f'<div class="big">{e(headline)}</div><div class="meta">{meta}</div></div>')


def finding_card(f: dict) -> str:
    lab, c, bg = SEVERITY[f["severity"]]
    owner = chip("Owner: " + f["owner"], "#334155", "#f1f5f9") if f.get("owner") else ""
    act = f'<div class="act">→ {e(f["action"])}</div>' if f.get("action") else ""
    return f'<div class="finding" style="--c:{c}">{chip(lab, c, bg)} {owner}<div class="msg">{e(f["message"])}</div>{act}</div>'


def conf_bar(v) -> str:
    if not isinstance(v, (int, float)):
        return ""
    color = "#16a34a" if v >= .8 else "#d97706" if v >= .6 else "#dc2626"
    return f'<span class="conf"><span style="width:{v * 100:.0f}%;background:{color}"></span></span>{v:.0%}'


def fields_table(x: dict) -> str:
    conf = x.get("confidence", {}) or {}
    rows = [("Vendor", x.get("vendor_name"), conf.get("vendor_name")),
            ("Vendor GSTIN", x.get("vendor_gstin"), None),
            ("Invoice number", x.get("invoice_number"), conf.get("invoice_number")),
            ("Invoice date", x.get("invoice_date"), conf.get("invoice_date")),
            ("PO number", x.get("po_number") or "— not printed", conf.get("po_number")),
            ("Subtotal (pre-tax)", inr(x.get("subtotal"), 2) if x.get("subtotal") is not None else "—", None),
            ("Tax", inr(x.get("tax_amount"), 2) if x.get("tax_amount") is not None else "—", None),
            ("Total", inr(x.get("total"), 2) if x.get("total") is not None else "—", conf.get("total")),
            ("Prices include tax", "Yes" if x.get("tax_inclusive") else "No", None)]
    body = "".join(f'<tr><td class="k">{e(k)}</td><td>{e(v)}</td><td style="text-align:right">{conf_bar(c)}</td></tr>'
                   for k, v, c in rows)
    return f'<table class="fields">{body}</table>'


def kpi(label, value, sub="", grad="#6366f1,#8b5cf6"):
    return (f'<div class="kpi" style="background:linear-gradient(135deg,{grad})"><div class="l">{e(label)}</div>'
            f'<div class="n">{e(value)}</div><div class="s">{e(sub)}</div></div>')


def tile_css(n: int) -> str:
    rules = "".join(f'.st-key-tile_{i} button {{ background: linear-gradient(135deg,{TILE_STYLE[i % len(TILE_STYLE)]}) !important; }}'
                    for i in range(n))
    return f"<style>{rules}</style>"


def _dur(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h {m}m" if h else f"{m} min"


def impact_panel(n, protected, dup_val, dup_n, over_val, risk_val, risk_n, manual_min, auto_s, per_inv) -> str:
    tiles = [("👯", inr(dup_val), "Duplicate payments blocked", f"{dup_n} invoice(s) caught"),
             ("📈", inr(over_val), "Over-billing flagged", "surcharges & price creep vs PO"),
             ("🛡️", inr(risk_val), "Risky payments blocked", f"{risk_n} fraud / unapproved-vendor case(s)"),
             ("⏱️", _dur(manual_min - auto_s / 60), "Analyst time saved",
              f"{n} × ~{per_inv} min by hand vs {auto_s:.0f} s here")]
    grid = "".join(f'<div class="itile"><div class="ii">{i}</div><div class="iv">{e(v)}</div><div class="il">{e(l)}</div>'
                   f'<div class="is">{e(s)}</div></div>' for i, v, l, s in tiles)
    return (f'<div class="impact"><div class="impact-kicker">BUSINESS IMPACT</div>'
            f'<div class="impact-big"><b>{inr(protected)}</b> of payments stopped before they went out</div>'
            f'<div class="impact-sub">across {n} invoices · checked in {auto_s:.0f} s instead of ~{_dur(manual_min)} by hand</div>'
            f'<div class="impact-grid">{grid}</div>'
            f'<div class="impact-note">Time saved assumes ~{per_inv} minutes of manual checking per invoice '
            f'(open, find the PO, compare, decide).</div></div>')


def batch_table(runs: list[dict]) -> str:
    rows = []
    for r in runs:
        label, _, icon = DECISION[r["decision"]]
        c = {"APPROVE": "#16a34a", "REVIEW": "#d97706", "REJECT": "#dc2626"}[r["decision"]]
        bg = {"APPROVE": "#dcfce7", "REVIEW": "#fef3c7", "REJECT": "#fee2e2"}[r["decision"]]
        short = {"APPROVE": "Approved", "REVIEW": "Needs review", "REJECT": "Rejected"}[r["decision"]]
        rows.append(f'<tr style="--c:{c}"><td>{e(r["file_name"][:2])}</td>'
                    f'<td class="nm">{e(r.get("vendor_name"))}<br><span style="font-weight:500;color:#64748b">'
                    f'{e(r.get("invoice_number"))}</span></td><td>{inr(r.get("total"), 0)}</td>'
                    f'<td>{chip(icon + " " + short, c, bg)}</td><td>{e(r["headline"].split(" — ", 1)[-1])}</td></tr>')
    return f'<table class="batch">{"".join(rows)}</table>'


@st.cache_data(show_spinner=False)
def pdf_preview(file_bytes: bytes, scale: float = 1.4):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(io.BytesIO(file_bytes))
    img = pdf[0].render(scale=scale).to_pil()
    pdf.close()
    return img
