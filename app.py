"""AP Autopilot — invoice PDF in, reasoned payment decision out.

Run locally:   streamlit run app.py
"""
import os
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# Load API keys from Streamlit secrets (deployment) into env before importing the engine.
try:
    for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
               "ANTHROPIC_MODEL", "GEMINI_MODEL", "OPENAI_MODEL"):
        if _k in st.secrets and not os.getenv(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:  # noqa: BLE001 — no secrets file locally is fine
    pass

import config as C  # noqa: E402
import db
import refdata  # noqa: E402
from extract import PROVIDER_LABEL, available_providers  # noqa: E402
from fmt import inr  # noqa: E402
from pipeline import STAGES, rank, run_pipeline  # noqa: E402
import ui  # noqa: E402

st.set_page_config(page_title="AP Autopilot · Invoice to Decision", page_icon="🧾", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)

SCENARIOS = {
    "01_acme_happy_path.pdf": ("Happy path", "Clean digital invoice that matches its PO exactly."),
    "02_brightpath_split_po_part1.pdf": ("Split PO · part 1", "First of two invoices billed against one PO."),
    "03_brightpath_split_po_part2.pdf": ("Split PO · part 2",
                                         "Second invoice on the same PO, with a surcharge nobody agreed to. Run part 1 first."),
    "04_northwind_price_increase_tax_inclusive.pdf": ("Price increase + tax-inclusive",
                                                      "Vendor raised its tariff on its own; prices shown including GST."),
    "05_greenleaf_scanned_no_po.pdf": ("Scanned, no PO number",
                                       "Image-only scan with no PO reference — the PO has to be inferred."),
    "06_acme_duplicate_resubmission.pdf": ("Duplicate resubmission",
                                           "Same invoice sent again as a 'copy', number written as 'INV 1043'. Run the happy path first."),
    "08_acme_bundled_lines_within_tolerance.pdf": ("Bundled lines, close but not exact",
                                                   "One consolidated line covering a 3-line PO, 1.4% over (delivery) — within tolerance."),
    "07_quickfix_unknown_vendor_missing_date.pdf": ("Unknown vendor",
                                                    "Supplier not in the vendor master, no invoice date, PO that doesn't exist."),
}

TILE_NAMES = {"04": "Price increase", "05": "Scanned, no PO", "06": "Duplicate", "08": "Bundled lines"}

# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
PAGES = [("Process invoice", ":material/bolt:"), ("Dashboard", ":material/space_dashboard:"),
         ("Rules & data", ":material/tune:")]
st.session_state.setdefault("page", "Process invoice")

with st.sidebar:
    st.markdown('<div class="brand"><div class="brand-logo">🧾</div><div><div class="brand-name">AP Autopilot</div>'
                '<div class="brand-tag">Invoice PDF → reasoned decision</div></div></div>', unsafe_allow_html=True)
    st.markdown('<div class="side-label">Menu</div>', unsafe_allow_html=True)
    for i, (name, icon) in enumerate(PAGES):
        active = st.session_state.page == name
        if st.button(name, key=f"nav_{i}", icon=icon, type="primary" if active else "secondary", width="stretch"):
            st.session_state.page = name
            st.session_state.scroll_top = True
            st.rerun()

    st.markdown('<div class="side-label">Extraction engine</div>', unsafe_allow_html=True)
    engine = st.segmented_control("Engine", ["Auto", "AI only", "Offline"], default="Auto",
                                  label_visibility="collapsed", key="engine",
                                  help="Auto uses the AI vision model and falls back to the PDF text layer / OCR "
                                       "if the model is unavailable.")
    mode = {"Auto": "auto", "AI only": "ai", "Offline": "local"}.get(engine or "Auto", "auto")
    provs = available_providers()
    if provs:
        st.markdown('<div class="ai-pill" style="background:#dcfce7;color:#166534"><span class="dotp" '
                    'style="background:#22c55e"></span>AI connected · ' + ", ".join(PROVIDER_LABEL[p] for p in provs)
                    + '</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="ai-pill" style="background:#e0e7ff;color:#3730a3"><span class="dotp" '
                    'style="background:#6366f1"></span>Offline reader (no AI key)</div>', unsafe_allow_html=True)

    st.markdown('<div class="side-label">Demo</div>', unsafe_allow_html=True)
    pace = st.toggle("Demo pacing", value=True, help="Adds a short pause between stages so viewers can follow along.")
    if st.button("Reset demo data", key="reset_btn", icon=":material/restart_alt:", width="stretch",
                 help="Clears run history and the PO ledger so the scenarios can be replayed from scratch."):
        db.reset()
        st.session_state.pop("last_run", None)
        st.toast("History cleared")

page = st.session_state.page


def header(title, sub):
    st.markdown(f'<div class="page-title">{title}</div><div class="page-sub">{sub}</div>', unsafe_allow_html=True)


def render_result(run: dict, file_bytes: bytes | None):
    st.markdown(ui.decision_banner(run), unsafe_allow_html=True)
    x = run["extracted"]
    tabs = st.tabs(["💡 Why this decision", "🧾 Extracted data", "📄 Document", "✉️ Vendor message"])
    with tabs[0]:
        actionable = [f for f in run["findings"] if f["severity"] != "info"]
        info = [f for f in run["findings"] if f["severity"] == "info"]
        if not actionable:
            st.markdown("Every check passed: the vendor is approved, the invoice is not a duplicate, it matches "
                        f"**{run['po_number']}** on price, and it fits within the PO's remaining balance.")
        for f in sorted(actionable, key=lambda f: (f["severity"] != "block", rank(f))):
            st.markdown(ui.finding_card(f), unsafe_allow_html=True)
        for f in info:
            st.markdown(ui.finding_card(f), unsafe_allow_html=True)
    with tabs[1]:
        c1, c2 = st.columns([1, 1.2])
        with c1:
            st.markdown(ui.fields_table(x), unsafe_allow_html=True)
            meta = x.get("_meta", {})
            st.caption(f"Read via **{meta.get('method', '—')}** · "
                       f"{'digital PDF' if meta.get('text_layer') else 'scanned image'} · {meta.get('pages', '?')} page(s)")
            if x.get("notes"):
                st.caption(f"📝 {x['notes']}")
        with c2:
            items = x.get("line_items") or []
            if items:
                df = pd.DataFrame(items).rename(columns={"description": "Description", "quantity": "Qty",
                                                         "unit_price": "Unit price", "amount": "Amount"})
                st.dataframe(df, hide_index=True, width="stretch",
                             column_config={"Unit price": st.column_config.NumberColumn(format="₹%.2f"),
                                            "Amount": st.column_config.NumberColumn(format="₹%.2f")})
            else:
                st.caption("No line items extracted.")
    with tabs[2]:
        if file_bytes:
            st.image(ui.pdf_preview(file_bytes), width="stretch")
    with tabs[3]:
        msg = x.get("_vendor_message")
        if msg:
            st.caption("Drafted automatically because the vendor needs to act. Review before sending.")
            st.markdown(f'<div class="emaildraft">{ui.e(msg)}</div>', unsafe_allow_html=True)
        else:
            st.caption("No vendor action needed for this invoice.")


def render_trace(stages: list[dict]):
    for i, s in enumerate(stages, 1):
        st.markdown(ui.stage_card(i, s["key"], s["title"], s["status"], s), unsafe_allow_html=True)


def run_js(js: str):
    """Run a tiny script in the page (used only for scrolling)."""
    try:
        st.iframe(f"<script>{js}</script>", height=1)
    except Exception:  # older Streamlit
        components.html(f"<script>{js}</script>", height=0)


def scroll_to(anchor_id: str):
    run_js("setTimeout(function(){var el=window.parent.document.getElementById('%s');"
           "if(el){el.scrollIntoView({behavior:'smooth',block:'start'});}},300);" % anchor_id)


def stage_grid():
    """Two-column grid of placeholders, filled row by row (1-2, 3-4, …)."""
    slots = []
    for _ in range(len(STAGES) // 2):
        a, b = st.columns(2, gap="medium")
        slots += [a.empty(), b.empty()]
    return slots


st.markdown('<div id="page-top"></div>', unsafe_allow_html=True)
if st.session_state.pop("scroll_top", False):
    scroll_to("page-top")


# --------------------------------------------------------------------------- #
# Page: Process invoice
# --------------------------------------------------------------------------- #
if page == "Process invoice":
    names = sorted(SCENARIOS)
    st.markdown('<div class="hero"><div class="hero-title">Process an <span>invoice</span></div>'
                '<div class="hero-sub">Drop in a vendor invoice and watch every check run live, with the '
                'evidence and the reasoning behind the decision.</div></div>', unsafe_allow_html=True)

    _, mid, _ = st.columns([0.6, 2.2, 0.6])
    run_now = None
    with mid:
        up = st.file_uploader("Invoice PDF", type=["pdf"], label_visibility="collapsed", key="uploader")
        if up:
            st.markdown(f'<div class="filechip"><div class="ic">📄</div><div><div class="nm">{ui.e(up.name)}</div>'
                        f'<div class="sz">{len(up.getvalue()) / 1024:.1f} KB · ready to process</div></div></div>',
                        unsafe_allow_html=True)
            if st.button("Run process", key="run_btn", icon=":material/play_arrow:", type="primary", width="stretch"):
                run_now = (up.getvalue(), up.name)
        st.markdown('<div class="or-line">or try a test invoice</div>', unsafe_allow_html=True)
    st.markdown(ui.tile_css(len(names)), unsafe_allow_html=True)
    tile_icons = ["🧾", "📦", "⛽", "📈", "🖨️", "👯", "❓", "🧺"]
    for row in range(0, len(names), 4):
        cols = st.columns(4, gap="small")
        for j, n in enumerate(names[row:row + 4]):
            i = row + j
            if cols[j].button(f"{tile_icons[i]}  {n[:2]} · {TILE_NAMES.get(n[:2], SCENARIOS[n][0])}", key=f"tile_{i}", width="stretch",
                              help=SCENARIOS[n][1]):
                run_now = ((C.SAMPLES_DIR / n).read_bytes(), n)

    st.markdown('<div id="live-run"></div>' + ui.section("⚡", "Live run", "each stage lights up as it executes"),
                unsafe_allow_html=True)
    track_slot = st.empty()
    slots = stage_grid()
    idx = {k: i for i, (k, _) in enumerate(STAGES)}

    if run_now:
        file_bytes, file_name = run_now
        scroll_to("live-run")
        states = {}
        track_slot.markdown(ui.track(states), unsafe_allow_html=True)
        for i, (k, t) in enumerate(STAGES, 1):
            slots[i - 1].markdown(ui.stage_card(i, k, t, "pending"), unsafe_allow_html=True)
        run = None
        for ev in run_pipeline(file_bytes, file_name, mode):
            if ev["type"] == "start":
                i = idx[ev["key"]]
                states[ev["key"]] = "running"
                track_slot.markdown(ui.track(states), unsafe_allow_html=True)
                slots[i].markdown(ui.stage_card(i + 1, ev["key"], ev["title"], "running"), unsafe_allow_html=True)
                if pace:
                    time.sleep(0.35)
            elif ev["type"] == "done":
                s = ev["stage"]
                i = idx[s["key"]]
                states[s["key"]] = s["status"]
                track_slot.markdown(ui.track(states), unsafe_allow_html=True)
                slots[i].markdown(ui.stage_card(i + 1, s["key"], s["title"], s["status"], s), unsafe_allow_html=True)
                if pace:
                    time.sleep(0.25)
            else:
                run = ev["run"]
        st.session_state["last_run"] = (run, file_bytes)
    elif "last_run" in st.session_state:
        run, _ = st.session_state["last_run"]
        track_slot.markdown(ui.track({s["key"]: s["status"] for s in run["stages"]}), unsafe_allow_html=True)
        for i, s in enumerate(run["stages"], 1):
            slots[i - 1].markdown(ui.stage_card(i, s["key"], s["title"], s["status"], s), unsafe_allow_html=True)
    else:
        track_slot.markdown(ui.track({}), unsafe_allow_html=True)
        for i, (k, t) in enumerate(STAGES, 1):
            slots[i - 1].markdown(ui.stage_card(i, k, t, "pending"), unsafe_allow_html=True)

    if "last_run" in st.session_state:
        run, fb = st.session_state["last_run"]
        st.markdown(ui.section("🏁", "Outcome", run["file_name"]), unsafe_allow_html=True)
        render_result(run, fb)

# --------------------------------------------------------------------------- #
# Page: Dashboard
# --------------------------------------------------------------------------- #
elif page == "Dashboard":
    header("Dashboard", "Every invoice the process has handled: status, outcomes, the review queue and PO balances.")
    runs = db.list_runs()
    if not runs:
        st.info("No runs yet. Process an invoice to see it here.")
        st.stop()

    n = len(runs)
    by_status = Counter(r["status"] for r in runs)
    auto = sum(1 for r in runs if r["decision"] == "APPROVE")
    approved_val = sum(r["total"] or 0 for r in runs if r["status"] in ("approved", "approved_by_reviewer"))
    avg_ms = sum(r["duration_ms"] for r in runs) / n
    k = st.columns(5)
    k[0].markdown(ui.kpi("Invoices processed", n, f"{len({r['vendor_id'] for r in runs if r['vendor_id']})} vendors", "#6366f1,#8b5cf6"),
                  unsafe_allow_html=True)
    k[1].markdown(ui.kpi("Straight-through rate", f"{auto / n:.0%}", f"{auto} auto-approved", "#0ea5e9,#06b6d4"), unsafe_allow_html=True)
    k[2].markdown(ui.kpi("Awaiting review", by_status.get("pending_review", 0), "in the queue below", "#f59e0b,#f97316"),
                  unsafe_allow_html=True)
    k[3].markdown(ui.kpi("Rejected", by_status.get("rejected", 0) + by_status.get("rejected_by_reviewer", 0),
                         "blocked before payment", "#ef4444,#f43f5e"), unsafe_allow_html=True)
    k[4].markdown(ui.kpi("Value approved", inr(approved_val), (f"avg {avg_ms:.0f} ms per invoice" if avg_ms < 1000 else f"avg {avg_ms / 1000:.1f}s per invoice"), "#10b981,#14b8a6"),
                  unsafe_allow_html=True)
    st.write("")

    c1, c2 = st.columns([1, 1.3], gap="large")
    with c1:
        st.markdown("##### Outcomes")
        dec = pd.DataFrame({"Decision": ["Approved", "Needs review", "Rejected"],
                            "Invoices": [sum(r["decision"] == d for r in runs) for d in ("APPROVE", "REVIEW", "REJECT")]})
        st.bar_chart(dec, x="Decision", y="Invoices", color="#8b5cf6", horizontal=True, height=162)
    with c2:
        st.markdown("##### Most common issues")
        codes = Counter(f["code"].replace("_", " ").capitalize() for r in runs for f in r["findings"]
                        if f["severity"] != "info")
        if codes:
            iss = pd.DataFrame(codes.most_common(6), columns=["Issue", "Count"])
            st.bar_chart(iss, x="Issue", y="Count", color="#f59e0b", horizontal=True, height=60 + 34 * len(iss))
        else:
            st.caption("No issues raised yet.")

    st.markdown("##### Run history")
    f1, f2, f3 = st.columns([1.2, 1, 1])
    status_filter = f1.multiselect("Status", list(ui.STATUS_LABEL), format_func=lambda s: ui.STATUS_LABEL[s][0])
    vendor_filter = f2.multiselect("Vendor", sorted({r["vendor_name"] or "?" for r in runs}))
    q = f3.text_input("Search invoice / PO", placeholder="e.g. INV-1043")
    view = [r for r in runs if (not status_filter or r["status"] in status_filter)
            and (not vendor_filter or (r["vendor_name"] or "?") in vendor_filter)
            and (not q or q.lower() in f"{r['invoice_number']} {r['po_number']} {r['file_name']}".lower())]
    label = {"approved": "✅ Auto-approved", "approved_by_reviewer": "✅ Approved (reviewer)",
             "pending_review": "🟠 Pending review", "rejected": "⛔ Rejected", "rejected_by_reviewer": "⛔ Rejected (reviewer)"}
    table = pd.DataFrame([{
        "Time": r["created_at"].replace("T", " ")[:16], "Vendor": r["vendor_name"], "Invoice": r["invoice_number"],
        "PO": r["po_number"], "Total": inr(r["total"], 2), "Status": label.get(r["status"], r["status"]),
        "Reason": r["headline"].split(" — ", 1)[-1], "Engine": r["extraction_method"], "id": r["id"]} for r in view])
    sel = st.dataframe(table.drop(columns=["id"]), hide_index=True, width="stretch",
                       on_select="rerun", selection_mode="single-row",
                       column_config={"Reason": st.column_config.TextColumn(width="large")})
    st.caption("Click a row to open its full trace.")

    pending = [r for r in runs if r["status"] == "pending_review"]
    chosen = None
    if sel and sel.selection.rows:
        chosen = view[sel.selection.rows[0]]

    st.markdown("##### PO balances")
    pos = refdata.purchase_orders()
    ledger = []
    for p in pos.to_dict("records"):
        billed = sum(b["pre_tax"] for b in db.billed_against_po(p["po_number"]))
        v = refdata.vendor_by_id(p["vendor_id"])
        ledger.append({"PO": p["po_number"], "Vendor": v["vendor_name"] if v else p["vendor_id"],
                       "Description": p["description"], "PO value": inr(p["po_amount_pre_tax"]), "Billed": inr(billed),
                       "Remaining": inr(p["po_amount_pre_tax"] - billed),
                       "Used": min(billed / p["po_amount_pre_tax"], 1.0)})
    st.dataframe(pd.DataFrame(ledger), hide_index=True, width="stretch", column_config={
        "Used": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
    st.caption("Balances count invoices approved automatically or by a reviewer. Pending and rejected invoices don't consume the PO.")

    if pending and not chosen:
        st.info(f"{len(pending)} invoice(s) waiting for review. Select one in the table above to review it.", icon="🟠")

    if chosen:
        st.divider()
        st.markdown(f"##### Run detail · {chosen['file_name']}  {ui.status_chip(chosen['status'])}",
                    unsafe_allow_html=True)
        con = db.connect()
        fb = con.execute("SELECT file_bytes FROM runs WHERE id=?", (chosen["id"],)).fetchone()[0]
        con.close()
        a, b = st.columns([1.3, 1], gap="large")
        with a:
            render_result(chosen, fb)
            if chosen["status"] == "pending_review":
                st.markdown("###### Reviewer decision")
                note = st.text_area("Note for the audit trail", placeholder="e.g. Price revision confirmed with procurement (email 12 Nov).")
                r1, r2 = st.columns(2)
                if r1.button("✓ Approve for payment", type="primary", width="stretch"):
                    db.review_run(chosen["id"], True, note)
                    st.toast("Approved — PO balance updated")
                    st.rerun()
                if r2.button("✕ Reject", width="stretch"):
                    db.review_run(chosen["id"], False, note)
                    st.toast("Rejected")
                    st.rerun()
            elif chosen.get("reviewer_note") or chosen.get("reviewed_at"):
                st.caption(f"Reviewed {chosen['reviewed_at']}: {chosen.get('reviewer_note') or '(no note)'}")
        with b:
            st.markdown("###### Stage trace")
            render_trace(chosen["stages"])

# --------------------------------------------------------------------------- #
# Page: Rules & data
# --------------------------------------------------------------------------- #
else:
    header("Rules & reference data",
           "The policies the process applies and the procurement data it checks against. Nothing is hidden in a prompt.")
    st.markdown("##### Decision policy")
    st.markdown(f"""
| Rule | Setting | Outcome when broken |
|---|---|---|
| Vendor must be in the vendor master and *approved* | fuzzy name match ≥ {C.VENDOR_MATCH_STRONG}/100 | Reject (route to onboarding) |
| GSTIN on invoice must match vendor master | exact | Reject (possible impersonation) |
| Invoice number + date must be present | GST requirement | Reject (vendor to reissue) |
| No duplicate invoice number per vendor | normalised (`INV 1043` ≡ `INV-1043`) | Reject |
| Same amount from same vendor within {C.SOFT_DUPLICATE_WINDOW_DAYS} days | soft duplicate | Review |
| PO must exist, be open and belong to the vendor | — | Reject |
| No PO printed | infer from vendor's open POs (≥ {C.PO_INFERENCE_MIN_CONFIDENCE:.0%} confidence) | Review with suggested PO |
| Invoice vs remaining PO balance | ≤ {C.AUTO_APPROVE_TOLERANCE_PCT}% over: approve · ≤ {C.REVIEW_TOLERANCE_PCT}%: review · more: reject | as stated |
| Unit price vs PO price | ± {C.UNIT_PRICE_TOLERANCE_PCT}% | Review |
| Line item not on the PO | fuzzy description match ≥ {C.LINE_MATCH_MIN_SCORE} | Review |
| Lines add up, pre-tax + tax = total | ± {inr(C.ARITHMETIC_TOLERANCE_INR)} | Review |
| GST rate is a valid slab | {', '.join(f'{r:g}%' for r in C.VALID_GST_RATES)} | Review |
| Extraction confidence | ≥ {C.LOW_CONFIDENCE_THRESHOLD:.0%} per field | Review |
| Auto-approval limit | {inr(C.MANAGER_APPROVAL_LIMIT_INR)} pre-tax | Review (finance controller) |
| Invoice age | ≤ {C.MAX_INVOICE_AGE_DAYS} days, not future-dated | Review |
""")
    st.caption("**Approve** only if nothing needs a human. **Review** if a person must confirm something. "
               "**Reject** if the invoice can't be paid as submitted. The AI only *reads* the document; every "
               "decision comes from the rules above, so the same invoice always gets the same answer.")
    t1, t2, t3 = st.tabs(["Vendor master", "Purchase orders", "PO lines"])
    t1.dataframe(refdata.vendors(), hide_index=True, width="stretch")
    t2.dataframe(refdata.purchase_orders(), hide_index=True, width="stretch")
    t3.dataframe(refdata.po_lines(), hide_index=True, width="stretch")
