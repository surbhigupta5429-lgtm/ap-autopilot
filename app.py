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
from pipeline import STAGES, rank, run_pipeline, run_sync
from extract import extract as read_invoice
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402
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
    "09_acme_bank_details_changed.pdf": ("Bank details changed (fraud)",
                                         "Everything matches the PO, but the bank account differs from the vendor master: "
                                         "classic payment-diversion fraud."),
    "10_acme_credit_note.pdf": ("Credit note", "A credit note for returned goods arrives in the AP inbox. Nothing to pay: "
                                               "it should be applied against the vendor's balance."),
    "11_northwind_wrong_gst_type.pdf": ("Wrong GST type", "Karnataka vendor billing a Maharashtra buyer with CGST+SGST "
                                                          "instead of IGST: we'd lose the GST input credit."),
    "12_greenleaf_billed_to_other_company.pdf": ("Billed to another company",
                                                 "A real vendor and PO, but the invoice is addressed to a sister company's GSTIN."),
    "07_quickfix_unknown_vendor_missing_date.pdf": ("Unknown vendor",
                                                    "Supplier not in the vendor master, no invoice date, PO that doesn't exist."),
}

TILE_NAMES = {"04": "Price increase", "05": "Scanned, no PO", "06": "Duplicate", "08": "Bundled lines",
              "09": "Bank details changed", "10": "Credit note", "11": "Wrong GST type", "12": "Billed to other company"}
BATCH_READERS = 4  # invoices read at the same time (kept low to respect AI rate limits)
MANUAL_MINUTES_PER_INVOICE = 8  # typical manual AP check per invoice (open, find PO, compare, decide)
OVERBILL_CODES = ("OVER_TOLERANCE", "OVER_LIMIT", "PRICE_VARIANCE", "UNORDERED_LINE")
RISK_CODES = ("BANK_DETAILS_CHANGED", "GSTIN_MISMATCH", "UNKNOWN_VENDOR", "VENDOR_NOT_ACTIVE", "BILL_TO_MISMATCH")

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
            try:
                st.image(ui.pdf_preview(file_bytes), width="stretch")
            except Exception:  # noqa: BLE001
                st.caption("Preview unavailable: the file could not be opened (corrupt or password-protected).")
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
    run_js("function up(){var el=window.parent.document.getElementById('page-top');if(!el)return;"
           "var p=el.parentElement;while(p&&p.scrollHeight<=p.clientHeight){p=p.parentElement;}"
           "if(p){p.scrollTo({top:0});}} setTimeout(up,300); setTimeout(up,900);")


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
    batch_files = None
    with mid:
        ups = st.file_uploader("Invoice PDFs", type=["pdf"], label_visibility="collapsed", key="uploader",
                               accept_multiple_files=True)
        if ups and len(ups) == 1:
            up = ups[0]
            st.markdown(f'<div class="filechip"><div class="ic">📄</div><div><div class="nm">{ui.e(up.name)}</div>'
                        f'<div class="sz">{len(up.getvalue()) / 1024:.1f} KB · ready to process</div></div></div>',
                        unsafe_allow_html=True)
            if st.button("Run process", key="run_btn", icon=":material/play_arrow:", type="primary", width="stretch"):
                run_now = (up.getvalue(), up.name)
        elif ups:
            size = sum(len(u.getvalue()) for u in ups) / 1024 / 1024
            st.markdown(f'<div class="filechip"><div class="ic">🗂️</div><div><div class="nm">{len(ups)} invoices selected</div>'
                        f'<div class="sz">{size:.1f} MB · read in parallel, decided one by one in order</div></div></div>',
                        unsafe_allow_html=True)
            if st.button(f"Process all {len(ups)} invoices", key="run_btn", icon=":material/play_arrow:", type="primary",
                         width="stretch"):
                batch_files = [(u.getvalue(), u.name) for u in ups]
        st.markdown('<div class="or-line">or try a test invoice</div>', unsafe_allow_html=True)
    st.markdown(ui.tile_css(len(names)), unsafe_allow_html=True)
    tile_icons = {"01": "🧾", "02": "📦", "03": "⛽", "04": "📈", "05": "🖨️", "06": "👯", "07": "❓", "08": "🧺", "09": "🏦",
                  "10": "↩️", "11": "🏛️", "12": "📮"}

    def tile_rows(subset, offset):
        hit = None
        for row in range(0, len(subset), 3):
            cols = st.columns(3, gap="small")
            for j, n in enumerate(subset[row:row + 3]):
                i = offset + row + j
                if cols[j].button(f"{tile_icons.get(n[:2], '📄')}  {n[:2]} · {TILE_NAMES.get(n[:2], SCENARIOS[n][0])}",
                                  key=f"tile_{i}", width="stretch", help=SCENARIOS[n][1]):
                    hit = ((C.SAMPLES_DIR / n).read_bytes(), n)
        return hit

    run_now = tile_rows(names[:9], 0) or run_now
    if len(names) > 9:
        _, midx, _ = st.columns([0.6, 2.2, 0.6])
        midx.markdown('<div class="or-line">more real-world checks</div>', unsafe_allow_html=True)
        run_now = tile_rows(names[9:], 9) or run_now

    _, mid2, _ = st.columns([0.6, 2.2, 0.6])
    with mid2:
        st.markdown('<div class="or-line">or process a whole batch</div>', unsafe_allow_html=True)
        batch_go = st.button(f"Run all {len(names)} test invoices as one batch", key="run_all",
                             icon=":material/playlist_play:", width="stretch",
                             help="Clears the demo history, then processes every test invoice in order, "
                                  "like a month's inbox arriving at once.")
    if batch_go:
        db.reset()
        batch_files = [((C.SAMPLES_DIR / n).read_bytes(), n) for n in names]
    if batch_files:
        st.session_state.pop("last_run", None)
        st.markdown('<div id="batch"></div>' + ui.section("📚", "Batch run", f"{len(batch_files)} invoices"),
                    unsafe_allow_html=True)
        scroll_to("batch")
        prog = st.progress(0.0, text="Reading invoices…")
        counts_slot, table_slot = st.empty(), st.empty()
        t0 = time.perf_counter()
        # 1) Read in parallel: the slow part (AI vision), and every file is independent.
        read = {}
        with ThreadPoolExecutor(max_workers=BATCH_READERS) as pool:
            futs = {pool.submit(read_invoice, fb, mode): i for i, (fb, _) in enumerate(batch_files)}
            for k, fut in enumerate(as_completed(futs), 1):
                read[futs[fut]] = fut.result()
                prog.progress(0.5 * k / len(batch_files),
                              text=f"Reading {k}/{len(batch_files)} in parallel ({BATCH_READERS} at a time)…")
        # 2) Decide one by one, in arrival order: duplicates and PO balances depend on what came before.
        results = []
        for i, (fb, fn) in enumerate(batch_files):
            prog.progress(0.5 + 0.5 * (i + 1) / len(batch_files), text=f"Deciding {i + 1}/{len(batch_files)} in order…")
            results.append(run_sync(fb, fn, mode, extracted=read[i]))
            counts_slot.markdown(ui.batch_counts(Counter(r["decision"] for r in results), len(batch_files)),
                                 unsafe_allow_html=True)
            if len(batch_files) <= 30 or i == len(batch_files) - 1 or i % 10 == 0:
                table_slot.markdown(ui.batch_table(results), unsafe_allow_html=True)
        secs = time.perf_counter() - t0
        prog.progress(1.0, text=f"Done: {len(batch_files)} invoices in {secs:.1f} s (~{secs / len(batch_files):.1f} s each)")
        st.session_state["batch_done"] = results
    elif st.session_state.get("batch_done") and not run_now:
        st.markdown(ui.section("📚", "Batch run", "last batch"), unsafe_allow_html=True)
        st.markdown(ui.batch_table(st.session_state["batch_done"]), unsafe_allow_html=True)
    if st.session_state.get("batch_done") and not run_now:
        if st.button("See the business impact on the Dashboard", key="to_dash", icon=":material/arrow_forward:",
                     type="primary"):
            st.session_state.page = "Dashboard"
            st.session_state.scroll_top = True
            st.rerun()

    st.markdown('<div id="live-run"></div>' + ui.section("⚡", "Live run", "each stage lights up as it executes"),
                unsafe_allow_html=True)
    track_slot = st.empty()
    slots = stage_grid()
    idx = {k: i for i, (k, _) in enumerate(STAGES)}

    if run_now:
        st.session_state.pop("batch_done", None)
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

    # ---- Business impact: what a buyer actually cares about ----
    def has(r, codes):
        return any(f["code"] in codes or (codes == "DUP" and f["code"].startswith("DUPLICATE")) for f in r["findings"])
    dup_runs = [r for r in runs if has(r, "DUP")]
    risk_runs = [r for r in runs if has(r, RISK_CODES) and r not in dup_runs]
    dup_val = sum(r["total"] or 0 for r in dup_runs)
    risk_val = sum(r["total"] or 0 for r in risk_runs)
    over_val = sum(max([f.get("amount") or 0 for f in r["findings"] if f["code"] in OVERBILL_CODES], default=0)
                   for r in runs if r not in dup_runs)
    protected = dup_val + risk_val + over_val
    manual_min = n * MANUAL_MINUTES_PER_INVOICE
    auto_s = sum(r["duration_ms"] for r in runs) / 1000
    st.markdown(ui.impact_panel(n, protected, dup_val, len(dup_runs), over_val, risk_val, len(risk_runs),
                                manual_min, auto_s, MANUAL_MINUTES_PER_INVOICE), unsafe_allow_html=True)

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

    # ---- Outputs finance can use directly ----
    st.markdown("##### Exports")
    e1, e2, _ = st.columns([1, 1, 1.2])
    pay_rows = []
    for r in runs:
        if r["status"] not in ("approved", "approved_by_reviewer"):
            continue
        v = refdata.vendor_by_id(r["vendor_id"]) or {}
        due = ""
        if r.get("invoice_date") and v.get("payment_terms_days"):
            due = (pd.Timestamp(r["invoice_date"]) + pd.Timedelta(days=int(v["payment_terms_days"]))).date().isoformat()
        pay_rows.append({"vendor_id": r["vendor_id"], "vendor_name": r["vendor_name"], "vendor_gstin": v.get("gstin"),
                         "bank_account": v.get("bank_account"), "ifsc": v.get("ifsc"),
                         "invoice_number": r["invoice_number"], "invoice_date": r["invoice_date"],
                         "po_number": r["po_number"], "amount_inr": r["total"], "due_date": due,
                         "approved_by": "reviewer" if r["status"] == "approved_by_reviewer" else "auto"})
    e1.download_button("Payment file (approved invoices)", pd.DataFrame(pay_rows).to_csv(index=False).encode(),
                       "payment_file.csv", "text/csv", icon=":material/payments:", width="stretch",
                       disabled=not pay_rows, help="Ready to load into the bank / ERP. Bank details come from the "
                                                   "vendor master, never from the invoice.")
    audit = pd.DataFrame([{"run_id": r["id"], "processed_at": r["created_at"], "file": r["file_name"],
                           "vendor": r["vendor_name"], "invoice_number": r["invoice_number"], "po": r["po_number"],
                           "total_inr": r["total"], "decision": r["decision"], "status": r["status"],
                           "reasons": " | ".join(f["message"] for f in r["findings"] if f["severity"] != "info"),
                           "reviewer_note": r.get("reviewer_note") or "", "engine": r["extraction_method"]}
                          for r in runs])
    e2.download_button("Audit log (all runs)", audit.to_csv(index=False).encode(), "audit_log.csv", "text/csv",
                       icon=":material/fact_check:", width="stretch")

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
| Document must be a tax invoice | credit note → apply to balance · proforma / quote / statement → not payable | Review / Reject |
| Invoice must be billed to us | buyer GSTIN = {C.BUYER_GSTIN} | Reject (vendor to re-issue) |
| Correct GST type | same state → CGST+SGST · different state → IGST | Review (input credit at risk) |
| Bank account must match vendor master | exact | Reject (verify by phone, possible fraud) |
| Invoice currency = PO currency | — | Review (FX approval) |
| File must be readable | not corrupt / password-protected | Review (manual entry) |
""")
    st.caption("**Approve** only if nothing needs a human. **Review** if a person must confirm something. "
               "**Reject** if the invoice can't be paid as submitted. The AI only *reads* the document; every "
               "decision comes from the rules above, so the same invoice always gets the same answer.")
    t1, t2, t3 = st.tabs(["Vendor master", "Purchase orders", "PO lines"])
    t1.dataframe(refdata.vendors(), hide_index=True, width="stretch")
    t2.dataframe(refdata.purchase_orders(), hide_index=True, width="stretch")
    t3.dataframe(refdata.po_lines(), hide_index=True, width="stretch")
