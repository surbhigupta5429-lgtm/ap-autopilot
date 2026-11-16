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

**The core design choice: the AI reads, the rules decide.** An AI vision model (Gemini, Claude or OpenAI) turns the PDF into structured fields, each with a confidence score. Every decision after that is deterministic Python against explicit, tunable rules (`engine/config.py`). That means:

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

## Test scenarios (`sample_invoices/`)

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
| 08 | Bundled lines, close but not exact | One consolidated line covering a 3-line PO, 1.4% over because of delivery charges. Matched on combined value; within the 2% tolerance | ✅ Approve (noted) |

**Order matters:** run 01 before 06, and 02 before 03. Use **Reset demo data** in the sidebar to start clean.

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
pip install -r requirements.txt          # plus: brew/apt install tesseract poppler
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add one API key (optional)
streamlit run app.py
```
`python scripts/make_invoices.py` regenerates the test invoices.

## Deploy (Streamlit Community Cloud, free)

1. Push this folder to a public GitHub repo.
2. Go to share.streamlit.io → **Create app** → choose the repo, branch `main`, file `app.py`.
3. **Advanced settings → Secrets:** paste `GEMINI_API_KEY = "..."` (or another provider's key).
4. Deploy. `packages.txt` installs Tesseract and Poppler automatically.

## Project layout

```
app.py                 Streamlit UI: process, dashboard, rules
ui.py                  styling + stage / decision components
engine/config.py       every business rule and threshold
engine/extract.py      AI extraction (3 providers) + text-layer / OCR fallback
engine/pipeline.py     the 8 stages, findings and decision logic
engine/db.py           SQLite run history + PO ledger
engine/refdata.py      vendor master, POs, PO lines (CSV in data/po/)
scripts/make_invoices.py   generates the messy test invoices
```

## What I'd build next

Email-inbox ingestion; 3-way match with goods receipt notes; a bank-detail change check against the vendor master (a classic fraud vector); learning per-vendor layouts from reviewer corrections; an ERP connector to post approved invoices; and SLA alerts on the review queue.
