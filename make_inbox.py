"""Generate a realistic 'month's inbox' of 100 invoices + the POs they bill against.

Mix (expected outcome):
  70 clean (30 single-invoice POs + 20 POs billed in two parts)   -> Approve
   8 unit-price creep (+4-6%)                                      -> Review
   4 no PO number printed                                          -> Review (PO inferred / ambiguous)
   2 credit notes                                                  -> Review
   2 wrong GST type (inter-state vendor charging CGST+SGST)        -> Review
   5 duplicates re-sent with the number written differently        -> Reject
   3 unknown vendors                                               -> Reject
   2 bank details changed                                          -> Reject
   2 billed to a sister company                                    -> Reject
   2 over-billing beyond 10% of the PO                             -> Reject
Usage: python make_inbox.py <out_pdf_dir> <po_csv_out> <po_lines_csv_out>
"""
import importlib.util
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mk", HERE / "make_invoices.py")
mk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mk)

random.seed(2026)
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

ACME = dict(vid="V001", name="Acme Office Supplies Pvt Ltd", layout=mk.layout_classic,
            addr=["Plot 12, MIDC Industrial Area, Andheri East", "Mumbai 400093  |  GSTIN 27AABCA1234F1Z5"],
            items=[("A4 copier paper 75gsm (box of 5 reams)", 1450), ("Laser toner cartridge 88A", 4200),
                   ("Stationery kit - executive", 950), ("Whiteboard markers (box of 10)", 380),
                   ("Archive box files (pack of 10)", 1250)])
BRIGHT = dict(vid="V002", name="BrightPath Logistics LLP", layout=mk.layout_minimal, addr=[],
              items=[("Freight services Mumbai-Pune", 40000), ("Freight services Mumbai-Nashik", 32000),
                     ("Warehouse handling charges", 18000), ("Last-mile delivery - Mumbai", 26000)])
NORTH = dict(vid="V003", name="Northwind Cloud Services Pvt Ltd", layout=mk.layout_inclusive, addr=[],
             items=[("Cloud hosting - staging cluster", 85000), ("Managed backup service", 22000),
                    ("Object storage 20 TB", 31000), ("24x7 managed support", 30000)])
GREEN = dict(vid="V004", name="Greenleaf Facility Management Pvt Ltd", layout=mk.layout_sparse,
             addr=["Unit 7, Lake View Estate, Powai, Mumbai 400076", "GSTIN 27AADCG3456P1Z1"],
             items=[("Housekeeping services - Floor 2", 52000), ("Pest control treatment - warehouse", 14000),
                    ("Window cleaning - full building", 23000), ("Garden maintenance", 9000)])
VENDORS = [ACME, BRIGHT, NORTH, GREEN]
INV_PREFIX = {"V001": "INV-", "V002": "BPL/2026/", "V003": "NW-INV-", "V004": "GFM-"}

po_rows, line_rows, invoices = [], [], []
po_seq, inv_seq = [3000], {"V001": 2000, "V002": 1500, "V003": 70000, "V004": 4000}


def new_po(v, lines, desc):
    po_seq[0] += 1
    po = f"PO-2026-{po_seq[0]}"
    total = sum(q * p for _, q, p in lines)
    po_rows.append(f"{po},{v['vid']},2026-10-{random.randint(10, 28):02d},open,INR,{total:.0f},{desc}")
    for i, (d, q, p) in enumerate(lines, 1):
        line_rows.append(f"{po},{i},{d},{q},{p}")
    return po


_used_prices = set()


def jitter(v, p):
    """Negotiated prices differ per PO; each price is unique so no two invoices share a total by accident."""
    step = 10 if v is ACME else 100
    while True:
        q = p + random.randint(0, 60) * step
        if (v["vid"], q) not in _used_prices:
            _used_prices.add((v["vid"], q))
            return q


def pick_lines(v, n=None):
    n = n or (1 if v in (NORTH, GREEN) else random.randint(1, 2))
    chosen = random.sample(v["items"], n)
    if v in (NORTH, GREEN):  # these layouts show one amount per line
        return [(d, 1, jitter(v, p)) for d, p in chosen]
    if v is BRIGHT:
        return [(d, random.randint(1, 3), jitter(v, p)) for d, p in chosen]
    return [(d, random.randint(3, 40), jitter(v, p)) for d, p in chosen]


def next_no(v):
    inv_seq[v["vid"]] += random.randint(1, 4)
    return f"{INV_PREFIX[v['vid']]}{inv_seq[v['vid']]}"


def day(d):
    return {"V001": f"{d:02d}-Nov-2026", "V002": f"{d:02d}/11/2026", "V003": f"Nov {d}, 2026",
            "V004": f"{d:02d}.11.2026"}


def add(v, inv_no, d, po, lines, kind, **extra):
    data = dict(vendor=v["name"], vendor_addr=v["addr"] or ["", ""], inv_no=inv_no, date=day(d)[v["vid"]],
                po=po, lines=lines, period="November 2026", **extra)
    if v is ACME:
        data["due"] = ""
    invoices.append((d, kind, v["layout"], data))


# 70 clean: 30 single-shot POs + 20 POs billed in two unequal parts, >7 days apart
for _ in range(30):
    v = random.choice(VENDORS)
    lines = pick_lines(v)
    po = new_po(v, lines, "Monthly supplies and services")
    add(v, next_no(v), random.randint(1, 16), po, lines, "clean")
for _ in range(20):
    v = random.choice([ACME, BRIGHT])
    d0, p0 = random.choice(v["items"])
    p0 = jitter(v, p0)
    q = random.randint(10, 60) if v is ACME else random.randint(3, 6)
    po = new_po(v, [(d0, q, p0)], "Split delivery order")
    q1 = int(q * 0.6)
    add(v, next_no(v), random.randint(1, 5), po, [(d0, q1, p0)], "clean")
    add(v, next_no(v), random.randint(13, 16), po, [(d0, q - q1, p0)], "clean")

# 8 price creep: invoice unit price +4-6% over the PO
for _ in range(8):
    v = random.choice([ACME, BRIGHT])
    lines = pick_lines(v, 1)
    po = new_po(v, lines, "Supplies order")
    d, q, p = lines[0]
    add(v, next_no(v), random.randint(6, 16), po, [(d, q, round(p * random.uniform(1.04, 1.06)))], "price_creep")

# 4 no PO number printed
for _ in range(4):
    v = random.choice([ACME, GREEN])
    lines = pick_lines(v, 1)
    new_po(v, lines, "Services order")
    add(v, next_no(v), random.randint(6, 16), "", lines, "no_po")

# 2 credit notes
for _ in range(2):
    d, p = random.choice(ACME["items"])
    add(ACME, f"CN-{random.randint(100, 999)}", random.randint(8, 16), "", [(f"Return: {d}", random.randint(2, 5), p)],
        "credit_note", title="CREDIT NOTE")

# 2 wrong GST type (Karnataka vendor charging CGST+SGST to a Maharashtra buyer)
for _ in range(2):
    lines = pick_lines(NORTH, 1)
    po = new_po(NORTH, lines, "Cloud services")
    invoices.append((random.randint(6, 16), "wrong_gst", mk.layout_classic, dict(
        vendor=NORTH["name"], vendor_addr=["Outer Ring Road, Bengaluru 560103", "Karnataka  |  GSTIN 29AACCN9012M1Z8"],
        inv_no=next_no(NORTH), date=day(10)["V001"], po=po, due="", lines=lines,
        bank="Bank: Kotak Mahindra Bank, A/C 7711224466, IFSC KKBK0000811")))

# 3 unknown vendors
for name in ["Speedy Couriers & Cargo", "Metro Electricals Co.", "Prime IT Rentals"]:
    add(dict(GREEN, name=name, addr=["Andheri West, Mumbai 400058", "Ph: 98200 55555"]),
        f"SC-{random.randint(100, 999)}", random.randint(3, 16), f"PO-2026-{random.randint(9000, 9999)}",
        [("Services rendered - November", 1, random.randint(80, 300) * 100)], "unknown_vendor")

# 2 bank details changed
for _ in range(2):
    lines = pick_lines(ACME, 1)
    po = new_po(ACME, lines, "Supplies order")
    add(ACME, next_no(ACME), random.randint(8, 16), po, lines, "bank_changed",
        bank="Bank: Yes Bank, A/C 0099881122334, IFSC YESB0000099",
        footer="IMPORTANT: our bank account has changed - please pay to the new account above.")

# 2 billed to a sister company
for _ in range(2):
    lines = pick_lines(GREEN, 1)
    po = new_po(GREEN, lines, "Facility services")
    invoices.append((random.randint(5, 16), "billed_to_other", mk.layout_classic, dict(
        vendor=GREEN["name"], vendor_addr=GREEN["addr"], inv_no=next_no(GREEN), date=day(9)["V001"], po=po, due="",
        lines=lines, bank="Bank: ICICI Bank, A/C 001505012345, IFSC ICIC0000015",
        bill_to=["Zenith Foods Pvt Ltd", "Accounts Payable, Plot 44, Turbhe MIDC", "Navi Mumbai 400705",
                 "GSTIN: 27AAACZ9999K1Z3"])))

# 2 over-billing beyond 10% (extra quantity)
for _ in range(2):
    d, p = random.choice(BRIGHT["items"])
    p = jitter(BRIGHT, p)
    po = new_po(BRIGHT, [(d, 2, p)], "Freight order")
    add(BRIGHT, next_no(BRIGHT), random.randint(6, 16), po, [(d, 3, p)], "over_limit")

# Arrival order by date; then the 5 duplicates (re-sent later, number written differently)
invoices.sort(key=lambda x: x[0])
clean_acme = [x for x in invoices if x[1] == "clean" and x[3]["vendor"] == ACME["name"]]
for orig in random.sample(clean_acme, 5):
    dup = dict(orig[3], inv_no=orig[3]["inv_no"].replace("-", " "), title="INVOICE (COPY)",
               footer="Gentle reminder - payment pending.")
    invoices.append((17, "duplicate", orig[2], dup))

expected = {"clean": "APPROVE", "price_creep": "REVIEW", "no_po": "REVIEW", "credit_note": "REVIEW",
            "wrong_gst": "REVIEW", "duplicate": "REJECT", "unknown_vendor": "REJECT", "bank_changed": "REJECT",
            "billed_to_other": "REJECT", "over_limit": "REJECT"}
manifest = []
for i, (d, kind, fn, data) in enumerate(invoices, 1):
    name = f"inbox_{i:03d}.pdf"
    fn(OUT / name, data)
    manifest.append(f"{name},{kind},{expected[kind]}")
(OUT.parent / "inbox_manifest.csv").write_text("file,scenario,expected\n" + "\n".join(manifest) + "\n")
Path(sys.argv[2]).write_text("\n".join(po_rows) + "\n")
Path(sys.argv[3]).write_text("\n".join(line_rows) + "\n")
print(len(invoices), "invoices;", len(po_rows), "POs")
