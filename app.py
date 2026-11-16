"""AP Autopilot — invoice PDF in, reasoned payment decision out.

Run locally:   streamlit run app.py
"""
import os
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

# Load API keys from Streamlit secrets (deployment) into env before importing the engine.
try:
    for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
               "ANTHROPIC_MODEL", "GEMINI_MODEL", "OPENAI_MODEL"):
        if _k in st.secrets and not os.getenv(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:  # noqa: BLE001 — no secrets file locally is fine
    pass

from engine import config as C  # noqa: E402
from engine import db, refdata  # noqa: E402
from engine.extract import PROVIDER_LABEL, available_providers  # noqa: E402
from engine.fmt import inr  # noqa: E402
from engine.pipeline import STAGES, rank, run_pipeline  # noqa: E402
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

# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### 🧾 AP Autopilot")
    st.caption("Invoice PDF → reasoned payment decision")
    page = st.radio("Navigate", ["Process invoice", "Dashboard", "Rules & data"], label_visibility="collapsed")
    st.divider()
    st.markdown("**Extraction engine**")
    provs = available_providers()
    mode_label = st.radio("Engine", ["Auto", "AI only", "Local only (offline)"], label_visibility="collapsed",
                          help="Auto uses an AI vision model when an API key is configured and falls back to "
                               "the PDF text layer / OCR if the model is unavailable.")
    mode = {"Auto": "auto", "AI only": "ai", "Local only (offline)": "local"}[mode_label]
    if provs:
        st.success("AI connected: " + ", ".join(PROVIDER_LABEL[p] for p in provs), icon="✅")
    else:
        st.info("No AI key configured — using local text-layer + OCR extraction.", icon="ℹ️")
    pace = st.toggle("Demo pacing", value=True, help="Adds a short pause between stages so viewers can follow along.")
    st.divider()
    if st.button("Reset demo data", width="stretch",
                 help="Clears run history and the PO ledger so the scenarios can be replayed from scratch."):
        db.reset()
        st.session_state.pop("last_run", None)
        st.toast("History cleared")


def header(title, sub):
    st.markdown(f'<div class="app-title">{title}</div><div class="app-sub">{sub}</div>', unsafe_allow_html=True)


def render_result(run: dict, file_bytes: bytes | None):
    st.markdown(ui.decision_banner(run), unsafe_allow_html=True)
    x = run["extracted"]
    tabs = st.tabs(["Why this decision", "Extracted data", "Document", "Vendor message"])
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
        st.markdown(ui.stage_card(i, s["title"], s["status"], s), unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Page: Process invoice
# --------------------------------------------------------------------------- #
if page == "Process invoice":
    header("Process an invoice",
           "Drop in a vendor invoice. Each stage runs live and shows its evidence; the decision is explained rule by rule.")

    left, right = st.columns([0.9, 1.4], gap="large")
    with left:
        st.markdown("##### 1 · Input")
        src = st.radio("Source", ["Upload a PDF", "Use a test invoice"], horizontal=True, label_visibility="collapsed")
        file_bytes, file_name = None, None
        if src == "Upload a PDF":
            up = st.file_uploader("Invoice PDF", type=["pdf"], label_visibility="collapsed")
            if up:
                file_bytes, file_name = up.getvalue(), up.name
        else:
            names = sorted(SCENARIOS)
            pick = st.selectbox("Test invoice", names,
                                format_func=lambda n: f"{n[:2]} · {SCENARIOS[n][0]}", label_visibility="collapsed")
            st.markdown(f'<div class="scenario">{ui.e(SCENARIOS[pick][1])}</div>', unsafe_allow_html=True)
            file_bytes, file_name = (C.SAMPLES_DIR / pick).read_bytes(), pick
        go = st.button("▶  Run process", type="primary", width="stretch", disabled=file_bytes is None)
        if file_bytes:
            with st.expander("Preview document", expanded=False):
                st.image(ui.pdf_preview(file_bytes), width="stretch")

    with right:
        st.markdown("##### 2 · Live run")
        slots = [st.empty() for _ in STAGES]
        if go and file_bytes:
            for i, (k, t) in enumerate(STAGES, 1):
                slots[i - 1].markdown(ui.stage_card(i, t, "pending"), unsafe_allow_html=True)
            idx = {k: i for i, (k, _) in enumerate(STAGES)}
            run = None
            for ev in run_pipeline(file_bytes, file_name, mode):
                if ev["type"] == "start":
                    i = idx[ev["key"]]
                    slots[i].markdown(ui.stage_card(i + 1, ev["title"], "running"), unsafe_allow_html=True)
                    if pace:
                        time.sleep(0.35)
                elif ev["type"] == "done":
                    s = ev["stage"]
                    i = idx[s["key"]]
                    slots[i].markdown(ui.stage_card(i + 1, s["title"], s["status"], s), unsafe_allow_html=True)
                    if pace:
                        time.sleep(0.25)
                else:
                    run = ev["run"]
            st.session_state["last_run"] = (run, file_bytes)
        elif "last_run" in st.session_state:
            run, _ = st.session_state["last_run"]
            for i, s in enumerate(run["stages"], 1):
                slots[i - 1].markdown(ui.stage_card(i, s["title"], s["status"], s), unsafe_allow_html=True)
        else:
            for i, (k, t) in enumerate(STAGES, 1):
                slots[i - 1].markdown(ui.stage_card(i, t, "pending"), unsafe_allow_html=True)

    if "last_run" in st.session_state:
        st.markdown("##### 3 · Outcome")
        run, fb = st.session_state["last_run"]
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
    k[0].markdown(ui.kpi("Invoices processed", n, f"{len({r['vendor_id'] for r in runs if r['vendor_id']})} vendors"),
                  unsafe_allow_html=True)
    k[1].markdown(ui.kpi("Straight-through rate", f"{auto / n:.0%}", f"{auto} auto-approved"), unsafe_allow_html=True)
    k[2].markdown(ui.kpi("Awaiting review", by_status.get("pending_review", 0), "in the queue below"),
                  unsafe_allow_html=True)
    k[3].markdown(ui.kpi("Rejected", by_status.get("rejected", 0) + by_status.get("rejected_by_reviewer", 0),
                         "blocked before payment"), unsafe_allow_html=True)
    k[4].markdown(ui.kpi("Value approved", inr(approved_val), (f"avg {avg_ms:.0f} ms per invoice" if avg_ms < 1000 else f"avg {avg_ms / 1000:.1f}s per invoice")),
                  unsafe_allow_html=True)
    st.write("")

    c1, c2 = st.columns([1, 1.3], gap="large")
    with c1:
        st.markdown("##### Outcomes")
        dec = pd.DataFrame({"Decision": ["Approved", "Needs review", "Rejected"],
                            "Invoices": [sum(r["decision"] == d for r in runs) for d in ("APPROVE", "REVIEW", "REJECT")]})
        st.bar_chart(dec, x="Decision", y="Invoices", color="#6366f1", horizontal=True, height=162)
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
