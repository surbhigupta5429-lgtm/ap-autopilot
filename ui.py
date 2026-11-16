"""Presentation helpers: CSS, stage cards, badges, previews."""
import html
import io

import streamlit as st

from fmt import inr

STATUS = {
    "pending": ("○", "#94a3b8", "#f8fafc", "Waiting"),
    "running": ("", "#2563eb", "#eff6ff", "Running"),
    "pass": ("✓", "#15803d", "#f0fdf4", "Passed"),
    "warn": ("!", "#b45309", "#fffbeb", "Attention"),
    "fail": ("✕", "#b91c1c", "#fef2f2", "Failed"),
    "skip": ("–", "#64748b", "#f8fafc", "Skipped"),
}
DECISION = {
    "APPROVE": ("Approved", "#15803d", "#f0fdf4", "✓"),
    "REVIEW": ("Needs review", "#b45309", "#fffbeb", "!"),
    "REJECT": ("Rejected", "#b91c1c", "#fef2f2", "✕"),
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

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stText { font-family: 'Inter', system-ui, sans-serif; }
.block-container { padding-top: 3.2rem; max-width: 1280px; }
h1, h2, h3 { letter-spacing: -0.02em; }
.app-title { font-size: 1.75rem; color: #0f172a; font-weight: 700; margin: 0; }
.app-sub { color: #64748b; margin: 2px 0 18px 0; font-size: .95rem; }

.stage { border: 1px solid #e2e8f0; border-left: 4px solid var(--c); background: var(--bg);
         border-radius: 10px; padding: 10px 14px; margin-bottom: 8px; transition: all .2s; }
.stage-head { display: flex; align-items: center; gap: 10px; }
.stage-icon { width: 26px; height: 26px; border-radius: 50%; background: var(--c); color: #fff;
              display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: .85rem;
              flex-shrink: 0; }
.stage.pending .stage-icon { background: #e2e8f0; color: #64748b; }
.stage-title { font-weight: 600; font-size: .95rem; color: #0f172a; }
.stage-num { color: #94a3b8; font-weight: 500; margin-right: 4px; }
.stage-ms { margin-left: auto; color: #94a3b8; font-size: .78rem; font-variant-numeric: tabular-nums; }
.stage-sum { color: #334155; font-size: .88rem; margin: 4px 0 0 36px; }
.stage details { margin: 6px 0 0 36px; }
.stage summary { cursor: pointer; color: #475569; font-size: .8rem; font-weight: 500; }
.kv { display: grid; grid-template-columns: minmax(140px, 30%) 1fr; gap: 3px 12px; font-size: .82rem;
      margin-top: 6px; }
.kv .k { color: #64748b; } .kv .v { color: #0f172a; word-break: break-word; }
.spinner { width: 14px; height: 14px; border: 2px solid #bfdbfe; border-top-color: #fff; border-radius: 50%;
           animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.stage.running { box-shadow: 0 0 0 3px #dbeafe; }

.chip { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600;
        color: var(--c); background: var(--bg); white-space: nowrap; }

.decision { border-radius: 14px; padding: 18px 22px; border: 1px solid var(--c); background: var(--bg);
            margin-bottom: 14px; }
.decision .label { font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; color: var(--c);
                   font-weight: 700; }
.decision .big { font-size: 1.35rem; font-weight: 700; color: #0f172a; margin-top: 4px; line-height: 1.35; }
.decision .meta { color: #475569; font-size: .88rem; margin-top: 8px; }

.finding { border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 14px; margin-bottom: 8px; background: #fff; }
.finding .msg { font-weight: 500; color: #0f172a; font-size: .9rem; margin-top: 4px; }
.finding .act { color: #475569; font-size: .84rem; margin-top: 3px; }

.kpi { border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px; background: #fff; }
.kpi .l { color: #64748b; font-size: .78rem; font-weight: 500; }
.kpi .n { font-size: 1.55rem; font-weight: 700; color: #0f172a; margin-top: 2px; font-variant-numeric: tabular-nums; }
.kpi .s { color: #94a3b8; font-size: .76rem; }

.conf { height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden; width: 70px; display: inline-block;
        vertical-align: middle; margin-right: 6px; }
.conf > span { display: block; height: 100%; }
table.fields { width: 100%; border-collapse: collapse; font-size: .86rem; }
table.fields td { padding: 6px 4px; border-bottom: 1px solid #f1f5f9; }
table.fields td.k { color: #64748b; width: 34%; }
.emaildraft { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: .8rem; background: #f8fafc;
              border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; color: #1e293b; }
.scenario { font-size: .82rem; color: #475569; background: #f8fafc; border-radius: 8px; padding: 8px 12px;
            border: 1px dashed #cbd5e1; }
</style>
"""


def e(x) -> str:
    return html.escape(str(x)) if x is not None else "—"


def chip(text, color, bg):
    return f'<span class="chip" style="--c:{color};--bg:{bg}">{e(text)}</span>'


def status_chip(status):
    label, c, bg = STATUS_LABEL.get(status, (status, "#475569", "#e2e8f0"))
    return chip(label, c, bg)


def stage_card(idx: int, title: str, state: str = "pending", stage: dict | None = None, open_details=False) -> str:
    icon, c, bg, _ = STATUS[state]
    icon_html = '<div class="spinner"></div>' if state == "running" else e(icon)
    ms = f'<span class="stage-ms">{stage["ms"]} ms</span>' if stage else (
        '<span class="stage-ms">running…</span>' if state == "running" else "")
    body = ""
    if stage:
        body += f'<div class="stage-sum">{e(stage["summary"])}</div>'
        rows = "".join(f'<div class="k">{e(k)}</div><div class="v">{e(v)}</div>' for k, v in stage["details"])
        for f in stage.get("findings", []):
            lab, fc, fbg = SEVERITY[f["severity"]]
            rows += f'<div class="k">{chip(lab, fc, fbg)}</div><div class="v">{e(f["message"])}</div>'
        if rows:
            body += f'<details {"open" if open_details else ""}><summary>Evidence</summary><div class="kv">{rows}</div></details>'
    elif state == "running":
        body = '<div class="stage-sum" style="color:#2563eb">Working…</div>'
    return (f'<div class="stage {state}" style="--c:{c};--bg:{bg}"><div class="stage-head">'
            f'<div class="stage-icon">{icon_html}</div><div class="stage-title"><span class="stage-num">{idx:02d}</span>'
            f'{e(title)}</div>{ms}</div>{body}</div>')


def decision_banner(run: dict) -> str:
    label, c, bg, icon = DECISION[run["decision"]]
    headline = run["headline"].split(" — ", 1)[-1]
    meta = (f'{e(run.get("vendor_name"))} · Invoice {e(run.get("invoice_number"))} · '
            f'PO {e(run.get("po_number"))} · Total {inr(run.get("total"), 2)} · processed in {run["duration_ms"]} ms')
    return (f'<div class="decision" style="--c:{c};--bg:{bg}"><div class="label">{icon} {label}</div>'
            f'<div class="big">{e(headline)}</div><div class="meta">{meta}</div></div>')


def finding_card(f: dict) -> str:
    lab, c, bg = SEVERITY[f["severity"]]
    owner = chip("Owner: " + f["owner"], "#334155", "#f1f5f9") if f.get("owner") else ""
    act = f'<div class="act">→ {e(f["action"])}</div>' if f.get("action") else ""
    return (f'<div class="finding">{chip(lab, c, bg)} {owner}<div class="msg">{e(f["message"])}</div>{act}</div>')


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


def kpi(label, value, sub=""):
    return f'<div class="kpi"><div class="l">{e(label)}</div><div class="n">{e(value)}</div><div class="s">{e(sub)}</div></div>'


@st.cache_data(show_spinner=False)
def pdf_preview(file_bytes: bytes, scale: float = 1.4):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(io.BytesIO(file_bytes))
    img = pdf[0].render(scale=scale).to_pil()
    pdf.close()
    return img
