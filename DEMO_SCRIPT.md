# 5-minute demo video script

**Before recording:** open the deployed app, click **Reset demo data**, turn **Demo pacing** on, and select "Use a test invoice".

| Time | On screen | What to say |
|---|---|---|
| 0:00–0:30 | Process page | "AP teams open every invoice, find the PO, check the numbers and decide. This does that end to end. The key design choice: **AI reads, rules decide.** A vision model extracts the fields; every decision is deterministic code, so it's auditable and repeatable." |
| 0:30–1:15 | Run **01 Happy path** | Narrate the stages as they light up. Open the *Evidence* on Extract (confidence per field) and on Price & balance (line-level price check). "Approved, with the due date calculated from the vendor's payment terms." |
| 1:15–2:00 | Run **02** then **03** (split PO) | "Same PO, second invoice. The system remembers what's already been billed: ₹80k of ₹1.2L. This one goes 2.3% over, and the reason is a fuel surcharge that isn't on the PO. It doesn't just say 'amount mismatch', it finds the line that causes it." |
| 2:00–2:40 | Run **05** scanned, no PO | Show the Document tab (it's a scan). "No text layer, no PO number. The PO is inferred by scoring the vendor's open POs on line similarity and amount fit: 94%. We don't auto-pay on a guess, so it goes to a human with the answer pre-filled." |
| 2:40–3:20 | Run **06** duplicate | "Same invoice resent as a 'copy', number written differently. Normalising catches it. This is the expensive Friday-afternoon mistake." Show the Vendor message tab. |
| 3:20–3:45 | Run **07** unknown vendor | "Not an approved vendor, no date, PO doesn't exist. Rejected, routed to onboarding, vendor email drafted." |
| 3:45–4:40 | Dashboard | KPIs → run history → click the BrightPath part 2 row → add a note → **Approve**. "Watch the PO balance update: the human decision feeds back into the ledger." |
| 4:40–5:00 | Rules & data | "Every threshold is visible and tunable by the AP lead, nothing hidden in a prompt. Next I'd add inbox ingestion, 3-way match with goods receipts, and bank-detail change detection." |

## Interview questions to be ready for

- **Why not let the LLM decide?** Non-deterministic, hard to audit, can't explain itself consistently to finance. The LLM does the part that's genuinely hard for code (reading messy layouts); rules do the part that must be exact.
- **What if the AI is down?** Sidebar → "Local only". It falls back to the text layer / OCR and the trace records the fallback.
- **Why % of PO value, not % of remaining balance?** On the last invoice of a split PO a small overage looks huge against a small remainder. Tolerance is a budget concept, so it's measured against the budget.
- **Why does an inferred PO go to review even at 94%?** "No PO, no pay" is standard AP control. Inference saves the reviewer the lookup; it doesn't replace the sign-off.
- **Why is missing date a reject on 07 but review on a scan?** On a readable PDF it's genuinely absent (an invalid GST invoice → the vendor must reissue). On OCR it might be a reading miss → a human checks.
- **How would this scale?** Inbox listener → queue → same pipeline; SQLite → Postgres; vendor/PO CSVs → ERP API.
