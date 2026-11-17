# AP Autopilot: invoice PDF → reasoned payment decision

**ASA Case Study · PS-1 (Finance / AP)**

Drop in a vendor invoice. The process reads it, checks it against the procurement system (vendor master, purchase orders, what has already been billed) and returns **Approve**, **Needs review** or **Reject**. Every stage and every rule that fired is shown on screen.

- **Live run view:** eight stages light up as they execute, each with its evidence.
- **Dashboard:** run history, straight-through rate, the review queue (approve or reject with an audit note) and live PO balances.
- **Rules & data:** every policy threshold and all reference data, visible to the buyer.

---

## How it works

```
PDF ─► 1 Receive ─► 2 Extract ─► 3 Validate ─► 4 Vendor ─► 5 Duplicate ─► 6 PO match ─► 7 Price & balance ─► 8 Decision
        hash        AI vision     required      master +    normalised     explicit or    2-way match vs     findings →
        file        (fallback:    fields, maths, GSTIN,      number, same   inferred from  PO lines + running   APPROVE /
                    text / OCR)   tax slab, date status      file, amount   open POs       PO balance           REVIEW / REJECT
```

**The core design choice: the AI reads, the rules decide.** An AI vision model (Gemini, Claude or OpenAI) turns the PDF into structured fields, each with a confidence score. Every decision after that is deterministic Python against explicit, tunable rules (`config.py`). Where the AI's reading contradicts the arithmetic (e.g. it says prices include tax but the lines add up to the pre-tax subtotal), the arithmetic wins. That means:

- the same invoice always gets the same answer, so the system is auditable;
- every outcome traces back to a named rule; nothing is buried in a prompt;
- the AI can be swapped or removed. With no API key, or if the provider is down, the process falls back to the PDF text layer, or to Tesseract OCR for scans, and records the fallback in the trace.

Each check raises **findings** with a severity:

| Severity | Meaning | Effect |
|---|---|---|
| `block` | The invoice cannot be paid as submitted | **Reject** |
| `review` | A human must confirm something | **Needs review** |
| `info` | Noted for the audit trail | none |

The decision is the worst severity found. Each finding names an **owner** (AP reviewer, Vendor, Procurement, Finance controller) and a **next action**. When the vendor must act, a reply email is drafted automatically.

The **PO ledger** is derived from approved invoices. That's what makes split-invoice tracking work: pending or rejected invoices never consume PO balance. When a reviewer approves an invoice, the balance updates immediately.

## Test scenarios

**Run all 12 at once** with the batch button; the Dashboard's *Business impact* panel then shows the money stopped and the analyst time saved.

Each vendor uses a different layout, label vocabulary ("Invoice No" / "Bill No." / "Invoice #" / "Inv No") and tax presentation (CGST+SGST, a single GST line, IGST-inclusive).

| # | Scenario | What it tests | Expected |
|---|---|---|---|
| 01 | Happy path | Clean digital invoice, exact PO match | ✅ Approve |
| 02 | Split PO · part 1 | First of two invoices against one PO | ✅ Approve (PO 67% used) |
| 03 | Split PO · part 2 | Cumulative billing goes 2.3% over the PO because of a "fuel surcharge" that isn't on the PO | 🟠 Review |
| 04 | Price increase, tax-inclusive | Prices printed incl. GST, so pre-tax is derived. Hosting is +5% vs the PO price (vendor revised its tariff on its own) | 🟠 Review |
| 05 | Scanned, no PO number | Image-only PDF with no text layer. PO inferred from the vendor's open POs by line similarity and amount fit (94%) | 🟠 Review, with suggested PO |
| 06 | Duplicate resubmission | Same invoice marked "COPY", number written `INV 1043` instead of `INV-1043` | ⛔ Reject (duplicate) |
| 07 | Unknown vendor | Not in the vendor master, no invoice date, non-existent PO | ⛔ Reject + vendor email |
| 09 | Bank details changed (fraud) | Perfect invoice that matches its PO, but the bank account differs from the vendor master. This is the classic "we've changed our bank" payment-diversion scam | ⛔ Reject, verify by phone |
| 10 | Credit note | A credit note for returned goods lands in the AP inbox; nothing to pay | 🟠 Review: apply to vendor balance |
| 11 | Wrong GST type | Karnataka vendor billing a Maharashtra buyer with CGST+SGST instead of IGST | 🟠 Review: input credit at risk |
| 12 | Billed to another company | Real vendor + PO, but the invoice is addressed to a sister company's GSTIN | ⛔ Reject: re-issue |
| 08 | Bundled lines, close but not exact | One consolidated line covering a 3-line PO, 1.4% over because of delivery charges. Matched on combined value; within the 2% tolerance | ✅ Approve (noted) |

**Order matters:** run 01 before 06, and 02 before 03. Use **Reset demo data** in the sidebar to start clean.

## What the process checks (and why)

Designed for hundreds of invoices a day: **extract only what a decision needs, run cheap checks first, and never pay on uncertainty.**

| Question | Check | If it fails |
|---|---|---|
| Can we read it? | file opens; fields extracted with confidence | Review (manual entry) |
| Is it a payable invoice? | document type: invoice vs credit note / proforma / quote / statement | Review or Reject |
| Is it ours? | bill-to GSTIN = our GSTIN | Reject |
| Is it complete and correct? | number, date, total present; lines add up; valid GST slab; date sane | Reject (vendor) / Review |
| Is the vendor real and approved? | vendor master match, status, GSTIN, **bank account**, correct GST type | Reject (fraud) / Review |
| Have we seen it before? | normalised number, same file, same amount within 7 days | Reject / Review |
| Is there a valid PO? | exists, open, same vendor (or inferred, then human-confirmed) | Reject / Review |
| Do the numbers fit? | currency, unit prices, unordered lines, bundled lines, remaining PO balance | Approve / Review / Reject by tolerance |
| Is it within authority? | auto-approval limit | Review |

**Output:** one decision (Approve / Needs review / Reject), every reason with an owner and next action, a vendor email when the vendor must act, a payment file (bank details from the vendor master, never from the invoice) and an audit log.

## Scale: hundreds of invoices

Drop many PDFs into the upload box at once (try the **100-invoice month inbox** pack). The batch runs in two phases:

1. **Read in parallel, AI only where needed**: every file is independent, so 4 are read at a time. Each one first goes through the free text-layer reader; only documents it can't fully read (scans, missing fields, numbers that don't add up) are sent to the AI. In the 100-invoice pack that's 2 of 100.
   - **Rate limiter:** AI calls are spaced to stay under `AI_MAX_RPM` (default 12/min), even with parallel readers.
   - **Circuit breaker:** if every model reports its quota is used up, AI pauses for 60 s and the offline reader / OCR takes over instead of hammering the API.
2. **Decide one by one, in arrival order**: duplicate detection and PO balances depend on what came before, so decisions are never parallel; two copies of the same invoice can never both be approved.

Results list problems first (rejected, then review, then approved) so a reviewer starts where the work is. The test pack (`make_inbox.py`) is a realistic month: 70 clean (incl. 20 split POs), 8 price creep, 4 without PO numbers, 2 credit notes, 2 wrong GST type, 5 disguised duplicates, 3 unknown vendors, 2 bank-detail frauds, 2 billed to a sister company, 2 over-billed. Expected 70 approved / 16 review / 14 rejected, and that's what the process returns.

In production this becomes: inbox listener → queue → the same pipeline, with Postgres instead of SQLite and the ERP instead of CSVs.

## Assumptions

- Buyer is *Zenith Retail Pvt Ltd* (Mumbai). Currency INR, GST regime.
- PO values are **pre-tax**. The invoice's pre-tax amount is compared with the PO's **remaining** balance.
- Tolerance is measured as % of PO value: ≤ 2% over approves (noted), 2–10% goes to review, > 10% rejects.
- Never auto-pay without a confirmed PO: an inferred PO always goes to a human, pre-filled with the suggestion.
- A missing invoice number or date on a *readable* document is a vendor problem (invalid GST invoice → reject). On an OCR'd scan it may be a reading problem, so it goes to review instead.
- Duplicates are checked against invoices that are approved or pending. A previously *rejected* invoice can be resubmitted after correction.
- Invoices above ₹5,00,000 pre-tax always need finance controller sign-off.

## Run locally

```bash
pip install -r requirements.txt          # plus Tesseract + Poppler for the OCR fallback
streamlit run app.py
```
Add an API key in `.streamlit/secrets.toml` (see `secrets.toml.example`). Without one, the app runs on the local text-layer / OCR reader.

## Deploy (Streamlit Community Cloud)

Repo → share.streamlit.io → **Create app** → main file `app.py` → **Secrets**:
```
GEMINI_API_KEY = "..."
GEMINI_MODEL = "gemini-flash-lite-latest"
```
`packages.txt` installs Tesseract and Poppler automatically. If the chosen Gemini model is busy or retired, the app retries, then tries other Flash models, then falls back to the local reader. The trace shows which one answered.

## Files

| File | What it does |
|---|---|
| `app.py` | Streamlit UI: process, dashboard, rules |
| `ui.py` | Styling + stage / decision components |
| `config.py` | Every business rule and threshold |
| `extract.py` | AI extraction (Gemini / Claude / OpenAI) + text-layer / OCR fallback |
| `pipeline.py` | The 8 stages, findings and decision logic |
| `db.py` | SQLite run history + PO ledger |
| `refdata.py` | Loads the vendor master and POs |
| `vendors.csv`, `purchase_orders.csv`, `po_lines.csv` | The "procurement system" |
| `01_…08_*.pdf` | Test invoices (see table above) |
| `make_invoices.py` | Script that generated the test invoices |

## What I'd build next

Email-inbox ingestion; 3-way match with goods receipt notes; a bank-detail change check against the vendor master (a classic fraud vector); learning per-vendor layouts from reviewer corrections; an ERP connector to post approved invoices; and SLA alerts on the review queue.
