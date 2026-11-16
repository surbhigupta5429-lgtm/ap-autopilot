"""Business rules in one place.

Every number here is a policy decision an AP lead would want to see and tune.
The UI renders this file's values on the "Rules & Data" page, so the logic is
never hidden from the buyer.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")


def now():
    """Local (IST) wall-clock time, naive, for display and storage."""
    return datetime.now(TZ).replace(tzinfo=None)

# Works whether files sit in folders (data/po, sample_invoices) or all at the top level.
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REF_DIR = DATA_DIR / "po" if (DATA_DIR / "po" / "vendors.csv").exists() else ROOT
DB_PATH = ROOT / "runs.db"
SAMPLES_DIR = ROOT / "sample_invoices" if (ROOT / "sample_invoices").exists() else ROOT

# Our own company (the buyer). Used to tell our GSTIN apart from the vendor's.
BUYER_NAME = "Zenith Retail Pvt Ltd"
BUYER_GSTIN = "27AAACZ4321R1Z9"

# ---- Amount matching -------------------------------------------------------
# Over-billing measured as % of the PO value, against what is still unbilled.
AUTO_APPROVE_TOLERANCE_PCT = 2.0     # <= 2% over remaining balance -> approve (noted)
REVIEW_TOLERANCE_PCT = 10.0          # 2–10% -> human review; > 10% -> reject
UNIT_PRICE_TOLERANCE_PCT = 2.0       # per-line unit price vs PO price
ARITHMETIC_TOLERANCE_INR = 1.0       # rounding slack for line sums / tax maths

# ---- Tax ---------------------------------------------------------------------
VALID_GST_RATES = [0.0, 5.0, 12.0, 18.0, 28.0]
GST_RATE_TOLERANCE_PCT = 0.25        # effective rate may differ by rounding

# ---- Approval authority -----------------------------------------------------
# Even a perfect match above this pre-tax amount needs a human sign-off.
MANAGER_APPROVAL_LIMIT_INR = 500000

# ---- Dates -------------------------------------------------------------------
MAX_INVOICE_AGE_DAYS = 180
FUTURE_DATE_GRACE_DAYS = 1

# ---- Matching ----------------------------------------------------------------
VENDOR_MATCH_STRONG = 90             # fuzzy score (0–100) to accept a vendor
VENDOR_MATCH_WEAK = 75               # between weak and strong -> review
PO_INFERENCE_MIN_CONFIDENCE = 0.70   # min score to suggest an inferred PO
PO_INFERENCE_MIN_MARGIN = 0.15       # best must beat runner-up by this much
LINE_MATCH_MIN_SCORE = 60            # fuzzy score to pair an invoice line with a PO line
SOFT_DUPLICATE_WINDOW_DAYS = 7       # same vendor + same total within N days

# ---- Extraction ----------------------------------------------------------------
LOW_CONFIDENCE_THRESHOLD = 0.6       # extracted fields below this are flagged
REQUIRED_FIELDS = ["vendor_name", "invoice_number", "invoice_date", "total"]
