"""Generate realistic, deliberately inconsistent test invoices.

Each vendor uses a different layout, label vocabulary and tax presentation —
the same messiness an AP team sees in real life. One invoice is converted into
an image-only "scanned" PDF (no text layer) to exercise the OCR / vision path.

Run:  python scripts/make_invoices.py
"""
from pathlib import Path
import random

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "sample_invoices"
OUT.mkdir(exist_ok=True)
W, H = A4


def inr(x: float) -> str:
    """Indian digit grouping: 1,14,500.00"""
    neg = x < 0
    x = abs(round(x, 2))
    whole, frac = f"{x:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return ("-" if neg else "") + whole + "." + frac


# --------------------------------------------------------------------------- #
# Layout A — classic bordered table, CGST/SGST split (Acme)
# --------------------------------------------------------------------------- #
def layout_classic(path, d):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, H - 25 * mm, d["vendor"])
    c.setFont("Helvetica", 9)
    y = H - 31 * mm
    for line in d["vendor_addr"]:
        c.drawString(20 * mm, y, line)
        y -= 4.5 * mm
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(W - 20 * mm, H - 25 * mm, d.get("title", "TAX INVOICE"))
    c.setFont("Helvetica", 10)
    meta = [("Invoice No:", d["inv_no"]), ("Invoice Date:", d.get("date", "")),
            ("PO Number:", d.get("po", "")), ("Due Date:", d.get("due", ""))]
    y = H - 33 * mm
    for k, v in meta:
        if not v:
            continue
        c.drawRightString(W - 55 * mm, y, k)
        c.drawRightString(W - 20 * mm, y, v)
        y -= 5 * mm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, H - 62 * mm, "Bill To:")
    c.setFont("Helvetica", 10)
    y = H - 67 * mm
    for line in ["Zenith Retail Pvt Ltd", "Accounts Payable, 4th Floor, Kalpataru Square",
                 "Andheri East, Mumbai 400069", "GSTIN: 27AAACZ4321R1Z9"]:
        c.drawString(20 * mm, y, line)
        y -= 4.5 * mm

    # table
    top = H - 95 * mm
    cols = [20, 30, 120, 140, 165, 190]
    c.setFillColor(colors.HexColor("#1f2937"))
    c.rect(20 * mm, top, 170 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    for x, h in zip(cols, ["#", "Description", "Qty", "Unit Price", "Amount"]):
        c.drawString((x + 1.5) * mm, top + 2.6 * mm, h)
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    y = top - 7 * mm
    for i, (desc, q, p) in enumerate(d["lines"], 1):
        c.drawString(21.5 * mm, y, str(i))
        c.drawString(31.5 * mm, y, desc)
        c.drawRightString(135 * mm, y, f"{q:g}")
        c.drawRightString(162 * mm, y, inr(p))
        c.drawRightString(188 * mm, y, inr(q * p))
        c.setStrokeColor(colors.HexColor("#d1d5db"))
        c.line(20 * mm, y - 2.5 * mm, 190 * mm, y - 2.5 * mm)
        y -= 8 * mm

    sub = sum(q * p for _, q, p in d["lines"])
    tax = round(sub * 0.09, 2)
    rows = [("Subtotal", sub), ("CGST @ 9%", tax), ("SGST @ 9%", tax), ("TOTAL (INR)", sub + 2 * tax)]
    y -= 4 * mm
    for k, v in rows:
        bold = k.startswith("TOTAL")
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        c.drawRightString(162 * mm, y, k)
        c.drawRightString(188 * mm, y, inr(v))
        y -= 6 * mm

    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, 30 * mm, "Bank: HDFC Bank, A/C 50200012345678, IFSC HDFC0000123")
    c.drawString(20 * mm, 25 * mm, f"Payment terms: Net 30. {d.get('footer', '')}")
    c.save()


# --------------------------------------------------------------------------- #
# Layout B — minimal, "Bill No." vocabulary, single GST line (BrightPath)
# --------------------------------------------------------------------------- #
def layout_minimal(path, d):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillColor(colors.HexColor("#0f766e"))
    c.rect(0, H - 18 * mm, W, 18 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(15 * mm, H - 12 * mm, d["vendor"].upper())
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 15 * mm, H - 12 * mm, "GSTIN 27AAKFB5678K1Z2")
    c.setFillColor(colors.black)

    c.setFont("Helvetica", 10)
    y = H - 32 * mm
    for k, v in [("Bill No.", d["inv_no"]), ("Bill Date", d.get("date", "")),
                 ("Ref PO", d.get("po", "")), ("Billing period", d.get("period", ""))]:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(15 * mm, y, k)
        c.setFont("Helvetica", 10)
        c.drawString(50 * mm, y, v)
        y -= 6 * mm
    c.drawString(120 * mm, H - 32 * mm, "Customer: Zenith Retail Pvt Ltd")
    c.drawString(120 * mm, H - 38 * mm, "Mumbai, Maharashtra")

    y = H - 65 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(15 * mm, y, "Particulars")
    c.drawRightString(140 * mm, y, "Qty")
    c.drawRightString(165 * mm, y, "Rate")
    c.drawRightString(195 * mm, y, "Value")
    c.line(15 * mm, y - 2 * mm, 195 * mm, y - 2 * mm)
    c.setFont("Helvetica", 10)
    y -= 8 * mm
    for desc, q, p in d["lines"]:
        c.drawString(15 * mm, y, desc)
        c.drawRightString(140 * mm, y, f"{q:g}")
        c.drawRightString(165 * mm, y, inr(p))
        c.drawRightString(195 * mm, y, inr(q * p))
        y -= 7 * mm
    c.line(15 * mm, y + 3 * mm, 195 * mm, y + 3 * mm)
    sub = sum(q * p for _, q, p in d["lines"])
    gst = round(sub * 0.18, 2)
    y -= 3 * mm
    for k, v in [("Taxable value", sub), ("GST 18%", gst), ("Amount payable", sub + gst)]:
        c.setFont("Helvetica-Bold" if k.startswith("Amount") else "Helvetica", 10)
        c.drawRightString(165 * mm, y, k)
        c.drawRightString(195 * mm, y, "Rs. " + inr(v))
        y -= 6 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(15 * mm, 20 * mm, "This is a computer generated bill. Payment within 45 days of bill date.")
    c.save()


# --------------------------------------------------------------------------- #
# Layout C — modern SaaS style, prices INCLUSIVE of GST (Northwind)
# --------------------------------------------------------------------------- #
def layout_inclusive(path, d):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 26)
    c.setFillColor(colors.HexColor("#4f46e5"))
    c.drawString(18 * mm, H - 28 * mm, "Invoice")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(W - 18 * mm, H - 22 * mm, d["vendor"])
    c.setFont("Helvetica", 8.5)
    c.drawRightString(W - 18 * mm, H - 27 * mm, "Outer Ring Road, Bengaluru 560103")
    c.drawRightString(W - 18 * mm, H - 31 * mm, "GSTIN 29AACCN9012M1Z8")

    c.setFont("Helvetica", 9)
    grid = [("Invoice #", d["inv_no"]), ("Issued", d.get("date", "")),
            ("Purchase Order", d.get("po", "")), ("Service month", d.get("period", ""))]
    x = 18 * mm
    for k, v in grid:
        c.setFillColor(colors.HexColor("#6b7280"))
        c.drawString(x, H - 45 * mm, k)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, H - 50 * mm, v)
        c.setFont("Helvetica", 9)
        x += 45 * mm

    y = H - 70 * mm
    c.setFillColor(colors.HexColor("#eef2ff"))
    c.rect(18 * mm, y - 2 * mm, 174 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y + 0.5 * mm, "Service")
    c.drawRightString(190 * mm, y + 0.5 * mm, "Amount (incl. GST)")
    c.setFont("Helvetica", 9.5)
    y -= 9 * mm
    total = 0
    for desc, q, p in d["lines"]:
        amt = round(q * p * 1.18, 2)
        total += amt
        c.drawString(20 * mm, y, desc)
        c.drawRightString(190 * mm, y, inr(amt))
        y -= 7 * mm
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(150 * mm, y, "Total due")
    c.drawRightString(190 * mm, y, "INR " + inr(total))
    y -= 6 * mm
    c.setFont("Helvetica", 8.5)
    igst = round(total - total / 1.18, 2)
    c.drawRightString(190 * mm, y, f"All amounts are inclusive of IGST @18% (IGST component: INR {inr(igst)})")
    c.setFont("Helvetica", 8)
    c.drawString(18 * mm, 22 * mm, d.get("footer", ""))
    c.save()


# --------------------------------------------------------------------------- #
# Layout D — small contractor, sparse, hand-made look (Greenleaf / QuickFix)
# --------------------------------------------------------------------------- #
def layout_sparse(path, d):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Times-Bold", 17)
    c.drawCentredString(W / 2, H - 22 * mm, d["vendor"])
    c.setFont("Times-Roman", 10)
    c.drawCentredString(W / 2, H - 28 * mm, d["vendor_addr"][0])
    c.drawCentredString(W / 2, H - 33 * mm, d["vendor_addr"][1])
    c.setFont("Times-Bold", 13)
    c.drawCentredString(W / 2, H - 44 * mm, "INVOICE")
    c.setFont("Times-Roman", 11)
    y = H - 56 * mm
    c.drawString(20 * mm, y, f"Inv No: {d['inv_no']}")
    if d.get("date"):
        c.drawRightString(W - 20 * mm, y, f"Dated: {d['date']}")
    y -= 6 * mm
    c.drawString(20 * mm, y, "To: Zenith Retail Pvt Ltd, Andheri East, Mumbai")
    if d.get("po"):
        y -= 6 * mm
        c.drawString(20 * mm, y, f"Your order ref: {d['po']}")
    y -= 12 * mm
    c.setFont("Times-Bold", 11)
    c.drawString(20 * mm, y, "Description of work")
    c.drawRightString(W - 20 * mm, y, "Rs.")
    c.line(20 * mm, y - 2 * mm, W - 20 * mm, y - 2 * mm)
    c.setFont("Times-Roman", 11)
    y -= 9 * mm
    for desc, q, p in d["lines"]:
        c.drawString(20 * mm, y, desc + (f"  ({q:g} x {inr(p)})" if q != 1 else ""))
        c.drawRightString(W - 20 * mm, y, inr(q * p))
        y -= 7 * mm
    c.line(20 * mm, y + 3 * mm, W - 20 * mm, y + 3 * mm)
    sub = sum(q * p for _, q, p in d["lines"])
    t = round(sub * 0.09, 2)
    y -= 4 * mm
    for k, v in [("Sub Total", sub), ("CGST 9%", t), ("SGST 9%", t), ("Grand Total", sub + 2 * t)]:
        c.setFont("Times-Bold" if k == "Grand Total" else "Times-Roman", 11)
        c.drawRightString(W - 60 * mm, y, k)
        c.drawRightString(W - 20 * mm, y, inr(v))
        y -= 6.5 * mm
    c.setFont("Times-Italic", 10)
    c.drawString(20 * mm, 40 * mm, "For " + d["vendor"])
    c.drawString(20 * mm, 30 * mm, "Authorised Signatory")
    c.save()


def make_scanned(src: Path, dst: Path):
    """Rasterise a PDF, rotate + add noise, save as image-only PDF (no text layer)."""
    from pdf2image import convert_from_path
    from PIL import ImageFilter, ImageEnhance
    random.seed(7)
    page = convert_from_path(str(src), dpi=150)[0].convert("L")
    page = page.rotate(1.3, expand=False, fillcolor=255)
    page = ImageEnhance.Contrast(page).enhance(0.85)
    px = page.load()
    w, h = page.size
    for _ in range(int(w * h * 0.004)):
        x, y = random.randrange(w), random.randrange(h)
        px[x, y] = random.randint(90, 190)
    page = page.filter(ImageFilter.GaussianBlur(0.6))
    page.convert("RGB").save(str(dst), "PDF", resolution=150)


INVOICES = [
    ("01_acme_happy_path.pdf", layout_classic, dict(
        vendor="Acme Office Supplies Pvt Ltd",
        vendor_addr=["Plot 12, MIDC Industrial Area, Andheri East", "Mumbai 400093  |  GSTIN 27AABCA1234F1Z5"],
        inv_no="INV-1043", date="05-Nov-2026", po="PO-2026-0101", due="05-Dec-2026",
        lines=[("A4 copier paper 75gsm (box of 5 reams)", 50, 1450), ("Laser toner cartridge 88A", 10, 4200)])),
    ("02_brightpath_split_po_part1.pdf", layout_minimal, dict(
        vendor="BrightPath Logistics LLP", inv_no="BPL/2026/0871", date="02/11/2026", po="PO-2026-0102",
        period="Oct - Nov 2026", lines=[("Freight services Mumbai-Pune (Oct, Nov)", 2, 40000)])),
    ("03_brightpath_split_po_part2.pdf", layout_minimal, dict(
        vendor="BrightPath Logistics LLP", inv_no="BPL/2026/0912", date="14/11/2026", po="PO-2026-0102",
        period="Dec 2026", lines=[("Freight services Mumbai-Pune (Dec)", 1, 40000), ("Fuel surcharge", 1, 2800)])),
    ("04_northwind_price_increase_tax_inclusive.pdf", layout_inclusive, dict(
        vendor="Northwind Cloud Services Pvt Ltd", inv_no="NW-INV-55821", date="Nov 3, 2026", po="PO-2026-0103",
        period="November 2026",
        footer="Note: revised hosting tariff effective 1 Nov 2026 as per our notice dated 15 Oct.",
        lines=[("Cloud hosting - production cluster", 1, 262500), ("24x7 managed support", 1, 30000)])),
    ("_greenleaf_clean.pdf", layout_sparse, dict(
        vendor="Greenleaf Facility Management Pvt Ltd",
        vendor_addr=["Unit 7, Lake View Estate, Powai, Mumbai 400076", "GSTIN 27AADCG3456P1Z1"],
        inv_no="GFM-3378", date="06.11.2026",
        lines=[("Housekeeping services - November 2026", 1, 85000), ("Pest control treatment", 1, 12000)])),
    ("06_acme_duplicate_resubmission.pdf", layout_classic, dict(
        vendor="Acme Office Supplies Pvt Ltd", title="INVOICE (COPY)",
        vendor_addr=["Plot 12, MIDC Industrial Area, Andheri East", "Mumbai 400093  |  GSTIN 27AABCA1234F1Z5"],
        inv_no="INV 1043", date="05-Nov-2026", po="PO-2026-0101", due="05-Dec-2026",
        footer="Gentle reminder - payment pending.",
        lines=[("A4 copier paper 75gsm (box of 5 reams)", 50, 1450), ("Laser toner cartridge 88A", 10, 4200)])),
    ("08_acme_bundled_lines_within_tolerance.pdf", layout_classic, dict(
        vendor="Acme Office Supplies Pvt Ltd",
        vendor_addr=["Plot 12, MIDC Industrial Area, Andheri East", "Mumbai 400093  |  GSTIN 27AABCA1234F1Z5"],
        inv_no="INV-1077", date="12-Nov-2026", po="PO-2026-0107", due="12-Dec-2026",
        footer="Includes delivery charges.",
        lines=[("Pantry supplies - November (consolidated, incl. delivery)", 1, 25350)])),
    ("07_quickfix_unknown_vendor_missing_date.pdf", layout_sparse, dict(
        vendor="QuickFix Electricals & Repairs",
        vendor_addr=["Shop 4, Saki Naka Junction, Mumbai 400072", "Mob: 98200 11223"],
        inv_no="QF-221", po="PO-2026-0199",
        lines=[("Emergency electrical repair - DB panel", 1, 18500)])),
]

if __name__ == "__main__":
    for name, fn, data in INVOICES:
        fn(OUT / name, data)
        print("wrote", name)
    make_scanned(OUT / "_greenleaf_clean.pdf", OUT / "05_greenleaf_scanned_no_po.pdf")
    (OUT / "_greenleaf_clean.pdf").unlink()
    print("wrote 05_greenleaf_scanned_no_po.pdf (image-only scan)")
