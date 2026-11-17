"""The invoice-to-decision process.

`run_pipeline` is a generator: it yields an event as each stage starts and
finishes, which is what drives the live run view in the UI. Each stage records:
  - status   pass / warn / fail / skip
  - summary  one plain-English line
  - details  the evidence (what it looked at, what it compared)
  - findings issues it raised, each with a severity:
        block  -> invoice cannot be paid (REJECT)
        review -> a human must look (NEEDS REVIEW)
        info   -> noted for the audit trail, no action

The final decision is a pure function of the findings, so every outcome can be
traced back to the specific rule that caused it.
"""
from __future__ import annotations

import hashlib
import itertools
import re
import time
import uuid
from datetime import date, datetime, timedelta

from rapidfuzz import fuzz

import config as C
import db
import refdata
from extract import extract
from fmt import inr, pct

STAGES = [
    ("ingest", "Receive document"),
    ("extract", "Extract invoice data"),
    ("validate", "Validate data quality"),
    ("vendor", "Verify vendor"),
    ("duplicate", "Check for duplicates"),
    ("po", "Match purchase order"),
    ("amounts", "Price & PO balance check"),
    ("decide", "Decision"),
]


# Which reason leads the headline when several apply (most decisive first).
PRIORITY = ["NOT_AN_INVOICE", "CREDIT_NOTE", "BILL_TO_MISMATCH", "DUPLICATE_FILE", "DUPLICATE_INVOICE", "BANK_DETAILS_CHANGED", "GSTIN_MISMATCH", "UNKNOWN_VENDOR", "VENDOR_NOT_ACTIVE",
            "PO_VENDOR_MISMATCH", "PO_NOT_FOUND", "PO_CLOSED", "PO_EXHAUSTED", "NO_OPEN_PO", "OVER_LIMIT",
            "MISSING_INVOICE_NUMBER", "MISSING_INVOICE_DATE", "EXTRACTION_FAILED", "PRICE_VARIANCE",
            "UNORDERED_LINE", "CURRENCY_MISMATCH", "WRONG_GST_TYPE", "OVER_TOLERANCE", "PO_INFERRED", "PO_AMBIGUOUS", "POSSIBLE_DUPLICATE",
            "ABOVE_APPROVAL_LIMIT"]


def rank(f):
    return PRIORITY.index(f["code"]) if f["code"] in PRIORITY else len(PRIORITY)


def norm_invoice_no(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"[^A-Z0-9]", "", s.upper()).lstrip("0") or None


class Ctx:
    """Everything the stages learn about one invoice."""

    def __init__(self, file_bytes, file_name, mode, today):
        self.file_bytes = file_bytes
        self.file_name = file_name
        self.mode = mode
        self.today = today or C.now().date()
        self.findings: list[dict] = []
        self.data: dict = {}
        self.meta: dict = {}
        self.vendor: dict | None = None
        self.po: dict | None = None
        self.po_inferred = False
        self.pre_tax = self.tax = self.total = None
        self.file_hash = hashlib.sha256(file_bytes).hexdigest()

    def flag(self, stage, severity, code, message, owner=None, action=None, amount=None):
        f = {"stage": stage, "severity": severity, "code": code, "message": message,
             "owner": owner, "action": action, "amount": amount}
        self.findings.append(f)
        return f


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #
def st_ingest(ctx: Ctx):
    seen = db.file_seen(ctx.file_hash)
    details = [("File", ctx.file_name), ("Size", f"{len(ctx.file_bytes) / 1024:.1f} KB"),
               ("Fingerprint (SHA-256)", ctx.file_hash[:16] + "…")]
    if seen:
        details.append(("Seen before", f"Identical file processed on {seen['created_at'][:16]} ({seen['status']})"))
    return "pass", "Document received and fingerprinted.", details


def st_extract(ctx: Ctx):
    # In a batch, reading happens in parallel beforehand; decisions still run one by one, in order.
    ctx.data, ctx.meta = ctx.pre_extracted or extract(ctx.file_bytes, ctx.mode)
    d, conf = ctx.data, ctx.data.get("confidence", {})
    doc_type = "Digital PDF (text layer)" if ctx.meta["text_layer"] else "Scanned image (no text layer)"
    details = [("Document type", doc_type), ("Pages", ctx.meta["pages"]), ("Extraction method", ctx.meta["method"])]
    failed = [a for a in ctx.meta["attempts"] if not a["ok"]]
    for a in failed:
        details.append(("Fallback", f"{a['method']} unavailable: {a.get('error', '')}"))

    if ctx.meta["method"] == "none":
        why = "it may be corrupt or password-protected" if ctx.meta.get("pages", 1) == 0 else "nothing legible was found"
        ctx.flag("extract", "review", "EXTRACTION_FAILED", f"Could not read the document ({why}).",
                 "AP reviewer", "Ask the vendor for an unlocked, readable PDF, or key the invoice in manually.")
        return "fail", "Could not read the document.", details

    low = [k for k, v in conf.items() if isinstance(v, (int, float)) and 0 < v < C.LOW_CONFIDENCE_THRESHOLD]
    if low:
        ctx.flag("extract", "review", "LOW_CONFIDENCE",
                 f"Low extraction confidence on: {', '.join(low)}.", "AP reviewer",
                 "Verify these fields against the document image.")
    if d.get("notes"):
        details.append(("Reader notes", d["notes"]))
    n = sum(1 for k in ("vendor_name", "invoice_number", "invoice_date", "po_number", "total") if d.get(k))
    status = "warn" if low else "pass"
    return status, f"Extracted {n}/5 key fields and {len(d.get('line_items', []))} line items via {ctx.meta['method']}.", details


def st_validate(ctx: Ctx):
    d = ctx.data
    details = []
    status = "pass"
    readable_source = ctx.meta.get("method", "").startswith(("Claude", "Gemini", "OpenAI", "PDF text"))

    # 0a. Is this actually a payable invoice? AP inboxes also receive credit notes, proformas, quotes, statements.
    doc = d.get("document_type") or "invoice"
    details.append(("Document type", doc.replace("_", " ").title()))
    if doc == "credit_note":
        ctx.flag("validate", "review", "CREDIT_NOTE",
                 "This is a credit note: the vendor owes us money. There is nothing to pay.", "AP reviewer",
                 "Apply the credit against the vendor's open balance / next payment instead of paying it.")
        status = "warn"
    elif doc in ("proforma", "quotation"):
        ctx.flag("validate", "block", "NOT_AN_INVOICE",
                 f"This is a {doc}, not a tax invoice, so it can't be paid or used for GST credit.", "Vendor",
                 "Ask the vendor for the final tax invoice.")
        status = "fail"
    elif doc in ("statement", "other"):
        ctx.flag("validate", "block", "NOT_AN_INVOICE",
                 f"This document is a {doc.replace('_', ' ')}, not an invoice.", "AP reviewer",
                 "Reconcile it against open invoices; do not pay from it.")
        status = "fail"

    # 0b. Is it addressed to us? Paying a sister company's bill is a classic mistake.
    buyer = (d.get("buyer_gstin") or "").upper()
    if buyer:
        ok = buyer == C.BUYER_GSTIN
        details.append(("Billed to", f"{buyer} {'✓ that is us' if ok else '✗ not our GSTIN (' + C.BUYER_GSTIN + ')'}"))
        if not ok:
            ctx.flag("validate", "block", "BILL_TO_MISMATCH",
                     f"Invoice is addressed to another company (GSTIN {buyer}), not {C.BUYER_NAME}.", "Vendor",
                     f"Return to the vendor: re-issue to {C.BUYER_NAME}, GSTIN {C.BUYER_GSTIN}, if it is really ours.")
            status = "fail"
    else:
        details.append(("Billed to", "Buyer GSTIN not printed"))

    # 1. Required fields
    missing = [f for f in C.REQUIRED_FIELDS if not d.get(f)]
    for f in missing:
        label = f.replace("_", " ")
        if f in ("invoice_number", "invoice_date") and readable_source:
            ctx.flag("validate", "block", "MISSING_" + f.upper(),
                     f"Invoice has no {label}. A GST tax invoice must carry a number and date.", "Vendor",
                     f"Ask the vendor to reissue the invoice with a valid {label}.")
        else:
            ctx.flag("validate", "review", "MISSING_" + f.upper(), f"Could not find the {label}.", "AP reviewer",
                     f"Confirm the {label} from the document.")
        status = "fail"
    details.append(("Required fields", "All present" if not missing else "Missing: " + ", ".join(missing)))

    # 2. Work out pre-tax / tax / total, handling tax-inclusive invoices
    total, tax, sub = d.get("total"), d.get("tax_amount"), d.get("subtotal")
    if sub is None and total is not None and tax is not None:
        sub = round(total - tax, 2)
        details.append(("Pre-tax amount", f"Derived as total − tax = {inr(sub, 2)}"
                        + (" (prices were tax-inclusive)" if d.get("tax_inclusive") else "")))
    if tax is None and sub is not None and total is not None:
        tax = round(total - sub, 2)
    if total is None and sub is not None and tax is not None:
        total = round(sub + tax, 2)
    ctx.pre_tax, ctx.tax, ctx.total = sub, tax, total
    details.append(("Amounts", f"Pre-tax {inr(sub, 2)} · Tax {inr(tax, 2)} · Total {inr(total, 2)}"))

    # 3. Arithmetic: lines add up, tax adds up
    items = d.get("line_items") or []
    if items and all(i.get("amount") is not None for i in items):
        line_sum = round(sum(i["amount"] for i in items), 2)
        # Don't trust the reader's "prices include tax?" answer blindly: let the arithmetic decide.
        near = lambda a, b: a is not None and b is not None and abs(a - b) <= C.ARITHMETIC_TOLERANCE_INR
        if d.get("tax_inclusive") and near(line_sum, sub) and not near(line_sum, total):
            d["tax_inclusive"] = False
            details.append(("Tax treatment", "Corrected to tax-exclusive: line items match the pre-tax subtotal"))
        elif not d.get("tax_inclusive") and near(line_sum, total) and not near(line_sum, sub) and tax:
            d["tax_inclusive"] = True
            details.append(("Tax treatment", "Corrected to tax-inclusive: line items match the grand total"))
        target = total if d.get("tax_inclusive") else sub
        if target is not None:
            diff = abs(line_sum - target)
            ok = diff <= C.ARITHMETIC_TOLERANCE_INR
            details.append(("Line items sum", f"{inr(line_sum, 2)} vs {'total' if d.get('tax_inclusive') else 'subtotal'} "
                            f"{inr(target, 2)} → {'✓' if ok else f'✗ off by {inr(diff, 2)}'}"))
            if not ok:
                ctx.flag("validate", "review", "LINES_DONT_ADD_UP",
                         f"Line items add up to {inr(line_sum, 2)} but the invoice says {inr(target, 2)}.",
                         "AP reviewer", "Check for a missing or mis-read line item.")
                status = "warn" if status == "pass" else status
    if sub is not None and tax is not None and total is not None:
        if abs(sub + tax - total) > C.ARITHMETIC_TOLERANCE_INR:
            ctx.flag("validate", "review", "TOTAL_MISMATCH",
                     f"Pre-tax + tax ({inr(sub + tax, 2)}) ≠ total ({inr(total, 2)}).", "AP reviewer",
                     "Recompute totals with the vendor.")
            status = "warn" if status == "pass" else status

    # 4. Tax rate sanity
    if sub and tax is not None:
        rate = tax / sub * 100
        nearest = min(C.VALID_GST_RATES, key=lambda r: abs(r - rate))
        ok = abs(nearest - rate) <= C.GST_RATE_TOLERANCE_PCT
        details.append(("Effective GST rate", f"{rate:.2f}% {'✓ standard slab' if ok else '✗ not a GST slab'}"))
        if not ok:
            ctx.flag("validate", "review", "ODD_TAX_RATE", f"Effective tax rate {rate:.2f}% is not a valid GST slab.",
                     "AP reviewer", "Confirm tax treatment with the vendor.")
            status = "warn" if status == "pass" else status

    # 5. Date sanity
    if d.get("invoice_date"):
        inv_date = date.fromisoformat(d["invoice_date"])
        age = (ctx.today - inv_date).days
        details.append(("Invoice date", f"{inv_date:%d %b %Y} ({age} days old)"))
        if age < -C.FUTURE_DATE_GRACE_DAYS:
            ctx.flag("validate", "review", "FUTURE_DATED", "Invoice is dated in the future.", "AP reviewer",
                     "Hold until the invoice date, or confirm with the vendor.")
            status = "warn" if status == "pass" else status
        elif age > C.MAX_INVOICE_AGE_DAYS:
            ctx.flag("validate", "review", "STALE_INVOICE", f"Invoice is {age} days old.", "AP reviewer",
                     "Check it wasn't already paid through another channel.")
            status = "warn" if status == "pass" else status

    summary = {"pass": "Data is complete and internally consistent.",
               "warn": "Data is usable but has inconsistencies.",
               "fail": "Required information is missing."}[status]
    return status, summary, details


def st_vendor(ctx: Ctx):
    name = ctx.data.get("vendor_name")
    if not name:
        return "skip", "No vendor name to check.", []
    best, best_score, matched_on = None, 0, None
    for v in refdata.vendors().to_dict("records"):
        for cand in [v["vendor_name"], *str(v["aliases"]).split("|")]:
            s = fuzz.token_set_ratio(name.lower(), cand.lower())
            if s > best_score:
                best, best_score, matched_on = v, s, cand
    details = [("Name on invoice", name),
               ("Closest master record", f"{best['vendor_name']} ({best['vendor_id']}) — match score {best_score:.0f}/100")]

    if best_score < C.VENDOR_MATCH_WEAK:
        ctx.flag("vendor", "block", "UNKNOWN_VENDOR",
                 f"'{name}' is not in the approved vendor master.", "Procurement",
                 "Route to vendor onboarding; do not pay until the vendor is approved.")
        return "fail", "Vendor is not an approved supplier.", details

    ctx.vendor = best
    status = "pass"
    if best_score < C.VENDOR_MATCH_STRONG:
        ctx.flag("vendor", "review", "WEAK_VENDOR_MATCH",
                 f"Vendor name only loosely matches '{best['vendor_name']}' (score {best_score:.0f}).",
                 "AP reviewer", "Confirm this is the same legal entity.")
        status = "warn"

    if best["status"] != "approved":
        ctx.flag("vendor", "block", "VENDOR_NOT_ACTIVE",
                 f"Vendor {best['vendor_name']} is '{best['status'].replace('_', ' ')}' in the vendor master.",
                 "Procurement", "Resolve the vendor's status before paying.")
        status = "fail"
    details.append(("Vendor status", best["status"]))

    gst = ctx.data.get("vendor_gstin")
    if gst:
        ok = gst.upper() == str(best["gstin"]).upper()
        details.append(("GSTIN check", f"{gst} {'✓ matches master' if ok else '✗ master has ' + best['gstin']}"))
        if not ok:
            ctx.flag("vendor", "block", "GSTIN_MISMATCH",
                     f"GSTIN on invoice ({gst}) differs from vendor master ({best['gstin']}).", "AP reviewer",
                     "Possible impersonation or wrong billing entity — verify with the vendor by phone.")
            status = "fail"
    else:
        details.append(("GSTIN check", "Not printed on invoice"))

    # GST type: same state -> CGST+SGST, different state -> IGST (else we lose the input tax credit).
    tt = ctx.data.get("tax_type")
    if tt in ("IGST", "CGST+SGST") and str(best.get("gstin") or "")[:2].isdigit():
        same_state = str(best["gstin"])[:2] == C.BUYER_GSTIN[:2]
        expected = "CGST+SGST" if same_state else "IGST"
        ok = tt == expected
        details.append(("GST type", f"{tt} {'✓' if ok else '✗'} (vendor state {best['gstin'][:2]}, ours {C.BUYER_GSTIN[:2]}"
                                    f" → {expected})"))
        if not ok:
            ctx.flag("vendor", "review", "WRONG_GST_TYPE",
                     f"Invoice charges {tt}, but a {'same' if same_state else 'different'}-state supply must use "
                     f"{expected}. We would lose the GST input credit.", "Vendor",
                     f"Ask the vendor to re-issue the invoice with {expected}.")
            status = "warn" if status == "pass" else status

    # Payment-diversion fraud: the #1 invoice scam is "our bank details have changed".
    acct = re.sub(r"\D", "", ctx.data.get("bank_account") or "")
    master_acct = re.sub(r"\D", "", str(best.get("bank_account") or ""))
    if acct and master_acct:
        ok = acct == master_acct
        details.append(("Bank account check", f"…{acct[-4:]} " + ("✓ matches vendor master" if ok
                                                                   else f"✗ vendor master has …{master_acct[-4:]}")))
        if not ok:
            ctx.flag("vendor", "block", "BANK_DETAILS_CHANGED",
                     f"Bank account on invoice (…{acct[-4:]}) differs from the vendor master (…{master_acct[-4:]}). "
                     f"This is the classic payment-diversion fraud pattern.", "AP reviewer",
                     "Do not pay. Call the vendor on the number in the vendor master (not the one on the invoice) "
                     "to verify, and update bank details only through vendor onboarding.")
            status = "fail"
    else:
        details.append(("Bank account check", "Not printed on invoice"))
    mine = [f["code"] for f in ctx.findings if f["stage"] == "vendor"]
    if status == "pass":
        summary = f"Approved vendor: {best['vendor_name']}."
    elif "BANK_DETAILS_CHANGED" in mine:
        summary = "Bank details don't match our records — possible fraud."
    elif status == "fail":
        summary = "Vendor cannot be paid as-is."
    elif "WEAK_VENDOR_MATCH" in mine:
        summary = f"Probable match: {best['vendor_name']} — needs confirmation."
    elif "WRONG_GST_TYPE" in mine:
        summary = f"Approved vendor: {best['vendor_name']}, but the wrong GST type is charged."
    else:
        summary = f"Approved vendor: {best['vendor_name']}, with an issue to check."
    return status, summary, details


def st_duplicate(ctx: Ctx):
    inv_norm = norm_invoice_no(ctx.data.get("invoice_number"))
    if not ctx.vendor:
        return "skip", "Skipped — vendor not identified.", []
    prior = db.live_invoices_for_vendor(ctx.vendor["vendor_id"])
    details = [("Normalised invoice no.", inv_norm or "—"),
               ("Live invoices from this vendor", str(len(prior)))]
    for p in prior:
        if p["file_hash"] == ctx.file_hash:
            ctx.flag("duplicate", "block", "DUPLICATE_FILE",
                     f"This exact file was already processed as {p['invoice_number']} ({p['status'].replace('_', ' ')}).", "AP reviewer",
                     "No action — do not pay twice.")
            return "fail", "Exact same document already processed.", details
        if inv_norm and p["invoice_number_norm"] == inv_norm:
            details.append(("Match", f"'{ctx.data['invoice_number']}' ≡ '{p['invoice_number']}' after normalising"))
            ctx.flag("duplicate", "block", "DUPLICATE_INVOICE",
                     f"Duplicate of invoice {p['invoice_number']} already {p['status'].replace('_', ' ')} "
                     f"on {p['created_at'][:10]}.", "Vendor",
                     "Tell the vendor it is already in process; do not pay again.")
            return "fail", f"Duplicate of {p['invoice_number']}.", details
    # Soft duplicate: different number, same amount, close dates
    if ctx.total and ctx.data.get("invoice_date"):
        d0 = date.fromisoformat(ctx.data["invoice_date"])
        for p in prior:
            if p["total"] and abs(p["total"] - ctx.total) < 1 and p["invoice_date"]:
                gap = abs((date.fromisoformat(p["invoice_date"]) - d0).days)
                if gap <= C.SOFT_DUPLICATE_WINDOW_DAYS:
                    ctx.flag("duplicate", "review", "POSSIBLE_DUPLICATE",
                             f"Same amount ({inr(ctx.total)}) as {p['invoice_number']} within {gap} days.",
                             "AP reviewer", "Confirm these are two separate deliveries.")
                    return "warn", "Possible duplicate (same amount, close dates).", details
    return "pass", "No duplicates found.", details


def _score_po(ctx: Ctx, po: dict) -> tuple[float, float, float]:
    lines = refdata.lines_for_po(po["po_number"])
    items = ctx.data.get("line_items") or []
    if items and lines:
        line_score = sum(max(fuzz.token_set_ratio(i["description"].lower(), l["description"].lower())
                             for l in lines) for i in items) / len(items) / 100
    else:
        line_score = 0.0
    remaining = po["po_amount_pre_tax"] - sum(b["pre_tax"] for b in db.billed_against_po(po["po_number"]))
    amt_score = 0.0
    if ctx.pre_tax and remaining > 0:
        amt_score = max(0.0, 1 - abs(ctx.pre_tax - remaining) / po["po_amount_pre_tax"])
    return round(0.6 * line_score + 0.4 * amt_score, 3), line_score, amt_score


def st_po(ctx: Ctx):
    po_no = ctx.data.get("po_number")
    details = []
    if po_no:
        po_no = re.sub(r"\s+", "-", po_no.strip().upper())
        po = refdata.po_by_number(po_no)
        details.append(("PO on invoice", po_no))
        if not po:
            ctx.flag("po", "block", "PO_NOT_FOUND", f"PO {po_no} does not exist in the procurement system.",
                     "Vendor", "Ask the vendor to quote a valid PO number.")
            return "fail", f"{po_no} not found.", details
        if ctx.vendor and po["vendor_id"] != ctx.vendor["vendor_id"]:
            owner = refdata.vendor_by_id(po["vendor_id"])
            ctx.flag("po", "block", "PO_VENDOR_MISMATCH",
                     f"{po_no} belongs to {owner['vendor_name'] if owner else po['vendor_id']}, not this vendor.",
                     "Vendor", "Ask the vendor to confirm the correct PO.")
            return "fail", "PO belongs to a different vendor.", details
        if po["status"] != "open":
            ctx.flag("po", "block", "PO_CLOSED", f"{po_no} is {po['status']}.", "Procurement",
                     "Reopen the PO or raise a new one.")
            return "fail", f"{po_no} is not open.", details
        ctx.po = po
        details.append(("PO description", po["description"]))
        details.append(("PO value (pre-tax)", inr(po["po_amount_pre_tax"])))
        return "pass", f"Matched to {po_no} (explicit reference).", details

    # No PO printed — try to infer from this vendor's open POs.
    if not ctx.vendor:
        return "skip", "No PO on invoice and vendor unknown — cannot infer.", details
    pos = refdata.purchase_orders()
    cands = pos[(pos["vendor_id"] == ctx.vendor["vendor_id"]) & (pos["status"] == "open")].to_dict("records")
    details.append(("PO on invoice", "None printed — inferring from vendor's open POs"))
    if not cands:
        ctx.flag("po", "block", "NO_OPEN_PO", "No PO on the invoice and the vendor has no open POs.",
                 "Procurement", "The requester must raise a PO before this can be paid.")
        return "fail", "No PO available.", details
    scored = sorted(((_score_po(ctx, p), p) for p in cands), key=lambda x: -x[0][0])
    for (s, ls, am), p in scored:
        details.append((f"Candidate {p['po_number']}",
                        f"score {s:.2f} (line similarity {ls:.2f}, amount fit {am:.2f}) — {p['description']}"))
    (best_s, _, _), best = scored[0]
    runner = scored[1][0][0] if len(scored) > 1 else 0.0
    if best_s >= C.PO_INFERENCE_MIN_CONFIDENCE and best_s - runner >= C.PO_INFERENCE_MIN_MARGIN:
        ctx.po, ctx.po_inferred = best, True
        ctx.flag("po", "review", "PO_INFERRED",
                 f"No PO on invoice; best match is {best['po_number']} (confidence {best_s:.0%}).", "AP reviewer",
                 f"Confirm {best['po_number']} with the requester; ask vendor to quote PO numbers in future.")
        return "warn", f"Inferred {best['po_number']} (confidence {best_s:.0%}) — needs confirmation.", details
    ctx.flag("po", "review", "PO_AMBIGUOUS", "No PO on invoice and no single open PO clearly matches.",
             "AP reviewer", "Identify the correct PO with the requester.")
    return "fail", "Could not confidently identify the PO.", details


def st_amounts(ctx: Ctx):
    if any(f["code"].startswith("DUPLICATE") for f in ctx.findings):
        return "skip", "Skipped — invoice is a duplicate.", []
    if any(f["code"] in ("CREDIT_NOTE", "NOT_AN_INVOICE", "BILL_TO_MISMATCH") for f in ctx.findings):
        return "skip", "Skipped — not a payable invoice for us.", []
    if not ctx.po or ctx.pre_tax is None:
        return "skip", "Skipped — no PO or no amount to compare.", []
    po = ctx.po
    raw_cur = str(ctx.data.get("currency") or "INR").upper()
    cur = "INR" if any(t in raw_cur for t in ("INR", "₹", "RS", "RUPEE")) else (re.sub(r"[^A-Z]", "", raw_cur)[:3] or "INR")
    if cur != str(po.get("currency", "INR")).upper():
        ctx.flag("amounts", "review", "CURRENCY_MISMATCH",
                 f"Invoice is in {cur} but {po['po_number']} is in {po.get('currency', 'INR')}.", "AP reviewer",
                 "Convert at the agreed rate and get the budget owner's approval before paying.")
        return "warn", f"Currency mismatch ({cur} vs {po.get('currency', 'INR')}) — amounts not compared.", \
            [("Invoice currency", cur), ("PO currency", po.get("currency", "INR"))]
    billed = db.billed_against_po(po["po_number"])
    billed_sum = sum(b["pre_tax"] for b in billed)
    remaining = po["po_amount_pre_tax"] - billed_sum
    details = [("PO value", inr(po["po_amount_pre_tax"])),
               ("Already billed", f"{inr(billed_sum)} across {len(billed)} invoice(s)"
                + (": " + ", ".join(b["invoice_number"] for b in billed) if billed else "")),
               ("Remaining balance", inr(remaining)),
               ("This invoice (pre-tax)", inr(ctx.pre_tax))]
    status = "pass"

    if remaining <= 0:
        ctx.flag("amounts", "block", "PO_EXHAUSTED", f"{po['po_number']} is fully billed already.",
                 "Vendor", "Ask the vendor which order this relates to; procurement may need a new PO.")
        return "fail", "PO has no remaining balance.", details

    over = ctx.pre_tax - remaining
    over_pct = over / po["po_amount_pre_tax"] * 100
    if over <= 0:
        details.append(("Balance check", f"✓ Within balance — {inr(-over)} left after this invoice"))
    elif over_pct <= C.AUTO_APPROVE_TOLERANCE_PCT:
        details.append(("Balance check", f"Over by {inr(over)} ({pct(over_pct)}) — within {C.AUTO_APPROVE_TOLERANCE_PCT}% tolerance"))
        ctx.flag("amounts", "info", "WITHIN_TOLERANCE", f"Over remaining balance by {inr(over)} ({pct(over_pct)}), within tolerance.")
    elif over_pct <= C.REVIEW_TOLERANCE_PCT:
        details.append(("Balance check", f"✗ Over by {inr(over)} ({pct(over_pct)} of PO)"))
        ctx.flag("amounts", "review", "OVER_TOLERANCE",
                 f"Exceeds remaining PO balance by {inr(over)} ({pct(over_pct)} of PO value; tolerance "
                 f"{C.AUTO_APPROVE_TOLERANCE_PCT}%).", "AP reviewer",
                 "Get budget owner approval for the excess or short-pay to the PO balance.", amount=over)
        status = "warn"
    else:
        details.append(("Balance check", f"✗ Over by {inr(over)} ({pct(over_pct)} of PO)"))
        ctx.flag("amounts", "block", "OVER_LIMIT",
                 f"Exceeds remaining PO balance by {inr(over)} ({pct(over_pct)}) — beyond the "
                 f"{C.REVIEW_TOLERANCE_PCT}% review limit.", "Vendor",
                 "Return to vendor; bill only against the remaining PO balance or get a PO amendment.", amount=over)
        status = "fail"

    # Line-level 2-way match
    po_lines = refdata.lines_for_po(po["po_number"])
    eff_rate = (ctx.tax / ctx.pre_tax) if (ctx.tax and ctx.pre_tax) else 0.18
    line_total = {l["line_no"]: l["quantity"] * l["unit_price"] for l in po_lines}
    for it in ctx.data.get("line_items") or []:
        amt = it["amount"] / (1 + eff_rate) if ctx.data.get("tax_inclusive") else it["amount"]
        # Bundled line? One invoice line whose amount is closer to the sum of 2+ PO lines than to any single line.
        single_gap = min((abs(amt - v) for v in line_total.values()), default=float("inf"))
        bundle = None
        for n in range(2, len(po_lines) + 1):
            for combo in itertools.combinations(po_lines, n):
                gap = abs(amt - sum(line_total[l["line_no"]] for l in combo))
                if gap < single_gap and (bundle is None or gap < bundle[0]):
                    bundle = (gap, combo)
        if bundle and bundle[0] / po["po_amount_pre_tax"] * 100 <= C.REVIEW_TOLERANCE_PCT:
            covered = ", ".join(l["description"] for l in bundle[1])
            details.append((f"Line: {it['description']}",
                            f"Bundled — covers {len(bundle[1])} PO lines ({covered}); matched on combined value "
                            f"{inr(sum(line_total[l['line_no']] for l in bundle[1]))}"))
            ctx.flag("amounts", "info", "BUNDLED_LINES",
                     f"'{it['description']}' bundles {len(bundle[1])} PO lines; unit prices can't be checked "
                     f"individually, so it was matched on combined value.")
            continue
        best = max(po_lines, key=lambda l: fuzz.token_set_ratio(it["description"].lower(), l["description"].lower()),
                   default=None)
        score = fuzz.token_set_ratio(it["description"].lower(), best["description"].lower()) if best else 0
        if not best or score < C.LINE_MATCH_MIN_SCORE:
            details.append((f"Line: {it['description']}", f"✗ not on the PO ({inr(it['amount'])})"))
            ctx.flag("amounts", "review", "UNORDERED_LINE",
                     f"'{it['description']}' ({inr(it['amount'])}) is not on {po['po_number']}.", "AP reviewer",
                     "Confirm this charge was agreed before paying it.", amount=amt)
            status = "warn" if status == "pass" else status
            continue
        qty = it.get("quantity") or 1
        unit = it.get("unit_price") if (it.get("unit_price") and not ctx.data.get("tax_inclusive")) else amt / qty
        var = (unit - best["unit_price"]) / best["unit_price"] * 100
        ok = abs(var) <= C.UNIT_PRICE_TOLERANCE_PCT
        details.append((f"Line: {it['description']}",
                        f"{'✓' if ok else '✗'} unit {inr(unit)} vs PO {inr(best['unit_price'])} ({pct(var)})"))
        if not ok and var > 0:
            ctx.flag("amounts", "review", "PRICE_VARIANCE",
                     f"Unit price for '{best['description']}' is {pct(var)} vs PO ({inr(unit)} vs {inr(best['unit_price'])}).",
                     "AP reviewer", "Check whether a price revision was approved by procurement.",
                     amount=round((unit - best["unit_price"]) * qty, 2))
            status = "warn" if status == "pass" else status

    summary = {"pass": "Amounts agree with the PO.",
               "warn": "Amounts differ from the PO beyond tolerance.",
               "fail": "Amounts cannot be paid against this PO."}[status]
    return status, summary, details


def st_decide(ctx: Ctx):
    if ctx.pre_tax and ctx.pre_tax > C.MANAGER_APPROVAL_LIMIT_INR and not any(
            f["severity"] == "block" for f in ctx.findings):
        ctx.flag("decide", "review", "ABOVE_APPROVAL_LIMIT",
                 f"Pre-tax amount {inr(ctx.pre_tax)} is above the {inr(C.MANAGER_APPROVAL_LIMIT_INR)} auto-approval limit.",
                 "Finance controller", "Needs finance controller sign-off.")
    blocks = sorted([f for f in ctx.findings if f["severity"] == "block"], key=rank)
    reviews = sorted([f for f in ctx.findings if f["severity"] == "review"], key=rank)
    vname = ctx.vendor["vendor_name"] if ctx.vendor else (ctx.data.get("vendor_name") or "unknown vendor")
    inv = ctx.data.get("invoice_number") or "(no number)"
    if blocks:
        decision = "REJECT"
        headline = f"Rejected — {blocks[0]['message']}"
    elif reviews:
        decision = "REVIEW"
        headline = f"Needs review — {len(reviews)} issue{'s' if len(reviews) > 1 else ''}: {reviews[0]['message']}"
    else:
        decision = "APPROVE"
        due = ""
        if ctx.data.get("invoice_date") and ctx.vendor:
            due_d = date.fromisoformat(ctx.data["invoice_date"]) + timedelta(days=int(ctx.vendor["payment_terms_days"]))
            due = f", due {due_d:%d %b %Y}"
        headline = f"Approved for payment — {inr(ctx.total)} to {vname}{due}"
    details = [("Invoice", f"{inv} from {vname}"), ("Blocking issues", str(len(blocks))),
               ("Issues for review", str(len(reviews)))]
    status = {"APPROVE": "pass", "REVIEW": "warn", "REJECT": "fail"}[decision]
    return status, headline, details, decision


STAGE_FUNCS = {"ingest": st_ingest, "extract": st_extract, "validate": st_validate, "vendor": st_vendor,
               "duplicate": st_duplicate, "po": st_po, "amounts": st_amounts}


def vendor_message(ctx: Ctx, decision: str) -> str | None:
    """Draft email back to the vendor when the vendor has to act."""
    asks = [f for f in ctx.findings if f.get("owner") == "Vendor"]
    if not asks:
        return None
    name = ctx.vendor["vendor_name"] if ctx.vendor else (ctx.data.get("vendor_name") or "Vendor")
    inv = ctx.data.get("invoice_number") or "your recent invoice"
    bullets = "\n".join(f"  • {f['message']} {f['action'] or ''}".rstrip() for f in asks)
    return (f"Subject: Invoice {inv} — action needed\n\nDear {name} team,\n\n"
            f"We received invoice {inv} but cannot process it for payment yet:\n{bullets}\n\n"
            f"Please reply with a corrected invoice and we will process it promptly.\n\n"
            f"Regards,\nAccounts Payable, {C.BUYER_NAME}")


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run_pipeline(file_bytes: bytes, file_name: str, mode: str = "auto", today: date | None = None, save: bool = True,
                 extracted: tuple | None = None):
    ctx = Ctx(file_bytes, file_name, mode, today)
    ctx.pre_extracted = extracted
    run_id = uuid.uuid4().hex[:10]
    stages = []
    decision = "REVIEW"
    for key, title in STAGES:
        yield {"type": "start", "key": key, "title": title}
        t0 = time.perf_counter()
        n_before = len(ctx.findings)
        if key == "decide":
            status, summary, details, decision = st_decide(ctx)
        elif key != "extract" and ctx.meta.get("method") == "none":
            status, summary, details = "skip", "Skipped — nothing was extracted.", []
        else:
            status, summary, details = STAGE_FUNCS[key](ctx)
        st = {"key": key, "title": title, "status": status, "summary": summary,
              "details": [list(x) for x in details], "findings": ctx.findings[n_before:],
              "ms": int((time.perf_counter() - t0) * 1000)}
        if key == "extract":
            st["extracted"] = {k: v for k, v in ctx.data.items()}
        stages.append(st)
        yield {"type": "done", "stage": st}

    d = ctx.data
    run = {
        "id": run_id, "created_at": C.now().isoformat(timespec="seconds"), "file_name": file_name,
        "file_hash": ctx.file_hash, "extraction_method": ctx.meta.get("method"),
        "vendor_id": ctx.vendor["vendor_id"] if ctx.vendor else None,
        "vendor_name": ctx.vendor["vendor_name"] if ctx.vendor else d.get("vendor_name"),
        "invoice_number": d.get("invoice_number"), "invoice_number_norm": norm_invoice_no(d.get("invoice_number")),
        "invoice_date": d.get("invoice_date"), "po_number": ctx.po["po_number"] if ctx.po else d.get("po_number"),
        "pre_tax": ctx.pre_tax, "tax": ctx.tax, "total": ctx.total, "decision": decision,
        "status": {"APPROVE": "approved", "REVIEW": "pending_review", "REJECT": "rejected"}[decision],
        "headline": stages[-1]["summary"], "findings": ctx.findings, "stages": stages,
        "extracted": {**d, "_meta": {k: v for k, v in ctx.meta.items() if k != "raw_text"},
                      "_vendor_message": vendor_message(ctx, decision), "_po_inferred": ctx.po_inferred},
        "duration_ms": sum(s["ms"] for s in stages),  # compute time only (excludes UI demo pacing)
    }
    if save:
        db.save_run(run, file_bytes)
    yield {"type": "complete", "run": run}


def run_sync(file_bytes, file_name, mode="auto", today=None, save=True, extracted=None) -> dict:
    for ev in run_pipeline(file_bytes, file_name, mode, today, save, extracted):
        if ev["type"] == "complete":
            return ev["run"]
