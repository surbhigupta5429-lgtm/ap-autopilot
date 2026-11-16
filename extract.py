"""Stage 2: turn a PDF into structured invoice data.

Strategy, in order of preference:
  1. AI extraction: Claude, Gemini or OpenAI read the PDF directly, including scans.
     The model only *reads*; it never decides. Each field gets a confidence score.
  2. Local fallback: the PDF text layer (pdfplumber), or Tesseract OCR for scans,
     parsed with label-aware rules. This keeps the process running if no API key
     is configured or the provider is down. That's a deliberate resilience choice.

Every attempt is recorded, so the UI can show *how* the data was obtained.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from datetime import datetime

import pdfplumber
import requests

from config import BUYER_GSTIN

FIELDS = ["document_type", "buyer_gstin", "tax_type", "vendor_name", "vendor_gstin", "bank_account", "ifsc", "invoice_number", "invoice_date", "po_number",
          "currency", "subtotal", "tax_amount", "total", "tax_inclusive", "line_items"]

PROMPT = """You are an accounts-payable document reader. Extract data from the attached invoice.

Return ONLY a JSON object with exactly these keys:
{
  "document_type": "invoice"|"credit_note"|"proforma"|"quotation"|"statement"|"other",  // what this document really is
  "buyer_gstin": string|null,          // the BUYER's / bill-to GSTIN, if printed
  "tax_type": "CGST+SGST"|"IGST"|"none"|null,  // which GST components are charged; null if just "GST" with no split
  "vendor_name": string|null,          // the SELLER issuing the invoice (not the buyer "Zenith Retail")
  "vendor_gstin": string|null,         // the SELLER's GSTIN/tax id, not the buyer's
  "bank_account": string|null,         // the SELLER's bank account number for payment (digits only), if printed
  "ifsc": string|null,                 // the bank IFSC code, if printed
  "invoice_number": string|null,       // exactly as printed, keep spaces/slashes
  "invoice_date": "YYYY-MM-DD"|null,   // the issue date, NOT the due date. Indian invoices are day-first.
  "po_number": string|null,            // only if explicitly printed; never guess
  "currency": string|null,
  "line_items": [{"description": string, "quantity": number|null, "unit_price": number|null, "amount": number}],
  "subtotal": number|null,             // pre-tax total if printed
  "tax_amount": number|null,           // total tax (sum CGST+SGST, or IGST, or GST)
  "total": number|null,                // final amount payable
  "tax_inclusive": boolean,            // true if line amounts already include tax
  "confidence": {"vendor_name":0-1,"invoice_number":0-1,"invoice_date":0-1,"po_number":0-1,"total":0-1,"line_items":0-1},
  "notes": string                      // anything unusual an AP clerk should know (e.g. price revision notices, 'copy' stamps)
}
Rules: numbers as plain numbers (no commas or currency symbols). If a field is missing or illegible,
use null and a low confidence. Never invent values. Do not infer a PO number from context."""


# --------------------------------------------------------------------------- #
# Text layer / OCR
# --------------------------------------------------------------------------- #
def pdf_text(file_bytes: bytes) -> tuple[str, int]:
    """Text layer + page count. A corrupt or password-protected PDF returns ("", 0) instead of crashing."""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
            return text, len(pdf.pages)
    except Exception:  # noqa: BLE001
        return "", 0


def ocr_text(file_bytes: bytes) -> str:
    import pytesseract
    from pdf2image import convert_from_bytes
    pages = convert_from_bytes(file_bytes, dpi=200)
    return "\n".join(pytesseract.image_to_string(p) for p in pages)


# --------------------------------------------------------------------------- #
# AI providers
# --------------------------------------------------------------------------- #
def available_providers() -> list[str]:
    out = []
    if os.getenv("ANTHROPIC_API_KEY"):
        out.append("claude")
    if os.getenv("GEMINI_API_KEY"):
        out.append("gemini")
    if os.getenv("OPENAI_API_KEY"):
        out.append("openai")
    return out


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("model did not return JSON")
    return json.loads(m.group(0))


def _call_claude(b64: str) -> dict:
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
            {"type": "text", "text": PROMPT}]}]},
        timeout=90,
    )
    r.raise_for_status()
    return _parse_json("".join(b.get("text", "") for b in r.json()["content"]))


GEMINI_FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.5-flash"]


def _gemini_once(model: str, b64: str) -> requests.Response:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "content-type": "application/json"}
    body = {"contents": [{"parts": [{"inline_data": {"mime_type": "application/pdf", "data": b64}},
                                    {"text": PROMPT}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
                                 # Reading an invoice needs no deep reasoning: keep "thinking" low for speed.
                                 "thinkingConfig": {"thinkingLevel": os.getenv("GEMINI_THINKING", "low")}}}
    r = requests.post(url, headers=headers, json=body, timeout=40)
    if r.status_code == 400 and "think" in r.text.lower():  # model without thinking levels: retry plainly
        body["generationConfig"].pop("thinkingConfig")
        r = requests.post(url, headers=headers, json=body, timeout=40)
    return r


def _call_gemini(b64: str) -> dict:
    """Try the configured model, then fall back through other Flash models if one is busy or retired."""
    preferred = os.getenv("GEMINI_MODEL")
    models = ([preferred] if preferred else []) + [m for m in GEMINI_FALLBACK_MODELS if m != preferred]
    errors = []
    for model in models:
        for attempt in range(2):  # one quick retry on "busy" responses
            try:
                r = _gemini_once(model, b64)
            except requests.Timeout:
                errors.append(f"{model}: timed out")
                break
            if r.status_code in (429, 500, 503) and attempt == 0:
                time.sleep(2)
                continue
            if r.status_code != 200:
                errors.append(f"{model}: HTTP {r.status_code}")
                break
            parts = r.json()["candidates"][0]["content"]["parts"]
            data = _parse_json("".join(p.get("text", "") for p in parts if not p.get("thought")))
            data["_model"] = model
            return data
    raise RuntimeError("; ".join(errors))


def _call_openai(b64: str) -> dict:
    model = os.getenv("OPENAI_MODEL", "gpt-4.1")
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "content-type": "application/json"},
        json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
              "messages": [{"role": "user", "content": [
                  {"type": "file", "file": {"filename": "invoice.pdf",
                                            "file_data": f"data:application/pdf;base64,{b64}"}},
                  {"type": "text", "text": PROMPT}]}]},
        timeout=90,
    )
    r.raise_for_status()
    return _parse_json(r.json()["choices"][0]["message"]["content"])


PROVIDERS = {"claude": _call_claude, "gemini": _call_gemini, "openai": _call_openai}
PROVIDER_LABEL = {"claude": "Claude (vision)", "gemini": "Gemini (vision)", "openai": "OpenAI (vision)"}


# --------------------------------------------------------------------------- #
# Local rule-based parser (fallback)
# --------------------------------------------------------------------------- #
AMT = r"(-?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})|-?\d+\.\d{2})"
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b")
DATE_PATTERNS = [
    (r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", "dmy"),
    (r"\b(\d{1,2})[- ]([A-Za-z]{3,9})[- ,]+(\d{4})\b", "d_mon_y"),
    (r"\b([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})\b", "mon_d_y"),
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", "ymd"),
]
TOTAL_LABELS = r"(grand total|total \(inr\)|amount payable|total due|invoice total|total amount|balance due|^total)"
SKIP_LINE = re.compile(r"(sub ?total|taxable|cgst|sgst|igst|\bgst\b|total|amount payable|balance|tds|round)", re.I)


def to_num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = re.sub(r"[^\d.\-]", "", str(s))
    try:
        return float(s) if s not in ("", ".", "-") else None
    except ValueError:
        return None


def parse_date(s: str | None) -> str | None:
    """Return ISO date from many formats; numeric dates are read day-first (India)."""
    if not s:
        return None
    s = s.strip()
    for pat, kind in DATE_PATTERNS:
        m = re.search(pat, s)
        if not m:
            continue
        a, b, c = m.groups()
        try:
            if kind == "dmy":
                return datetime(int(c), int(b), int(a)).date().isoformat()
            if kind == "ymd":
                return datetime(int(a), int(b), int(c)).date().isoformat()
            if kind == "d_mon_y":
                return datetime.strptime(f"{a} {b[:3]} {c}", "%d %b %Y").date().isoformat()
            if kind == "mon_d_y":
                return datetime.strptime(f"{a[:3]} {b} {c}", "%b %d %Y").date().isoformat()
        except ValueError:
            continue
    return None


def _label_value(lines: list[str], label_re: str, value_re: str) -> str | None:
    """Value on the same line after the label, or in the next line under a header row."""
    for i, line in enumerate(lines):
        m = re.search(label_re + r"\s*[:#.]?\s*" + value_re, line, re.I)
        if m:
            return m.group(m.lastindex).strip()
    return None


def heuristic_parse(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    conf: dict[str, float] = {}

    # Invoice number: label on same line; else a header row ("Invoice # Issued ...") with values below.
    inv_val = r"((?=[A-Z0-9/\- ]*\d)[A-Z0-9][A-Z0-9/\-]*(?: \d+)?)"
    inv = _label_value(lines, r"(invoice\s*(?:no\.?|number|#)|inv\.?\s*no\.?|bill\s*no\.?)", inv_val)
    if not inv:
        for i, line in enumerate(lines[:-1]):
            if re.search(r"invoice\s*#|invoice\s*no", line, re.I) and not re.search(r"\d", line):
                cand = [t for t in lines[i + 1].split() if re.search(r"\d", t) and re.search(r"[A-Za-z]", t)
                        and not t.upper().startswith("PO")]
                if cand:
                    inv = cand[0]
                    break
    conf["invoice_number"] = 0.8 if inv else 0.0

    # Invoice date: prefer labelled, ignore due dates.
    date = None
    for i, line in enumerate(lines):
        if re.search(r"due", line, re.I):
            continue
        if re.match(r"^.{0,40}\b(invoice date|bill date|dated|issued|date)\b", line, re.I):
            date = parse_date(re.split(r"date|dated|issued", line, flags=re.I)[-1])
            if not date and not re.search(r"\d", line) and i + 1 < len(lines):  # header row -> values below
                date = parse_date(lines[i + 1])
            if date:
                break
    if not date:  # header-row layout: first full date in the document that is not a due date
        for line in lines:
            if not re.search(r"due", line, re.I) and parse_date(line):
                date = parse_date(line)
                break
    conf["invoice_date"] = 0.8 if date else 0.0

    po = None
    m = re.search(r"\bPO[-/ ]?\d{4}[-/]\d{3,5}\b", text, re.I)
    if m:
        po = m.group(0).upper().replace(" ", "-")
    else:
        po = _label_value(lines, r"(p\.?o\.?\s*(?:no\.?|number|#)?|purchase order|order ref)", r"([A-Z0-9][A-Z0-9/\-]{3,})")
    conf["po_number"] = 0.8 if po else 0.0

    all_gstins = list(dict.fromkeys(GSTIN_RE.findall(text)))
    gstins = [g for g in all_gstins if g != BUYER_GSTIN]
    vendor_gstin = gstins[0] if gstins else None
    buyer_gstin = BUYER_GSTIN if BUYER_GSTIN in all_gstins else (gstins[1] if len(gstins) > 1 else None)
    low = text.lower()
    if "credit note" in low:
        document_type = "credit_note"
    elif "proforma" in low or "pro-forma" in low:
        document_type = "proforma"
    elif "quotation" in low:
        document_type = "quotation"
    elif "statement of account" in low:
        document_type = "statement"
    else:
        document_type = "invoice"
    if re.search(r"\bIGST\b", text):
        tax_type = "IGST"
    elif re.search(r"\bCGST\b", text) and re.search(r"\bSGST\b", text):
        tax_type = "CGST+SGST"
    else:
        tax_type = None
    m = re.search(r"(?:a/c|account)(?:\s*(?:no\.?|number))?\s*[:#]?\s*(\d[\d ]{7,20}\d)", text, re.I)
    bank_account = re.sub(r"\D", "", m.group(1)) if m else None
    m = re.search(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", text)
    ifsc = m.group(1) if m else None

    # Vendor: first line that looks like a business name (skip document titles).
    vendor = None
    for line in lines[:6]:
        clean = re.sub(r"\b(tax invoice|invoice|gstin.*|\(copy\))\b", "", line, flags=re.I).strip(" -|")
        if re.search(r"(pvt|ltd|llp|limited|inc|services|supplies|repairs|logistics|&)", clean, re.I) and \
                "zenith" not in clean.lower():
            vendor = clean
            break
    conf["vendor_name"] = 0.8 if vendor else 0.0

    def amount_after(label_re):
        for line in lines:
            if re.search(label_re, line, re.I):
                nums = re.findall(AMT, line)
                if nums:
                    return to_num(nums[-1])
        return None

    total = amount_after(TOTAL_LABELS)
    conf["total"] = 0.8 if total else 0.0
    subtotal = amount_after(r"(sub ?total|taxable value)")
    tax_inclusive = bool(re.search(r"inclusive of (i|c|s)?gst|incl\.? gst", text, re.I))
    tax = 0.0
    found_tax = False
    for line in lines:
        if re.search(r"\b(cgst|sgst|igst|gst)\b", line, re.I) and not re.search(r"gstin", line, re.I):
            if tax_inclusive:
                m = re.search(r"component[^\d]*" + AMT, line, re.I)
                if m:
                    tax, found_tax = to_num(m.group(1)), True
                continue
            nums = re.findall(AMT, line)
            if nums:
                tax += to_num(nums[-1])
                found_tax = True

    # Line items: "desc qty unit amount" or "desc amount".
    items = []
    for line in lines:
        if SKIP_LINE.search(line) or re.search(r"(bank|a/c|ifsc|gstin|mob)", line, re.I):
            continue
        m = re.match(r"^(?:\d{1,2}\s+)?(.+?)\s+(\d+(?:\.\d+)?)\s+" + AMT + r"\s+" + AMT + r"$", line)
        if m:
            items.append({"description": m.group(1), "quantity": float(m.group(2)),
                          "unit_price": to_num(m.group(3)), "amount": to_num(m.group(4))})
            continue
        m = re.match(r"^([A-Za-z0-9].{3,}?)\s+" + AMT + r"$", line)
        if m and re.search(r"[A-Za-z]{3,}", m.group(1)):
            items.append({"description": m.group(1), "quantity": 1.0, "unit_price": None,
                          "amount": to_num(m.group(2))})
    conf["line_items"] = 0.75 if items else 0.0

    notes = []
    if re.search(r"\(copy\)|duplicate|reminder", text, re.I):
        notes.append("Document is marked as a copy / reminder.")
    if re.search(r"revised|tariff|price (increase|revision)", text, re.I):
        notes.append("Vendor mentions a price revision.")

    return {"document_type": document_type, "buyer_gstin": buyer_gstin, "tax_type": tax_type,
            "vendor_name": vendor, "vendor_gstin": vendor_gstin, "bank_account": bank_account, "ifsc": ifsc, "invoice_number": inv, "invoice_date": date,
            "po_number": po, "currency": "INR", "line_items": items, "subtotal": subtotal,
            "tax_amount": tax if found_tax else None, "total": total, "tax_inclusive": tax_inclusive,
            "confidence": conf, "notes": " ".join(notes)}


# --------------------------------------------------------------------------- #
# Normalise model output to a single shape
# --------------------------------------------------------------------------- #
def normalise(d: dict) -> dict:
    out = {k: d.get(k) for k in FIELDS}
    out["confidence"] = d.get("confidence") or {}
    out["notes"] = d.get("notes") or ""
    for k in ("subtotal", "tax_amount", "total"):
        out[k] = to_num(out[k])
    out["tax_inclusive"] = bool(out.get("tax_inclusive"))
    out["invoice_date"] = parse_date(out["invoice_date"]) if out["invoice_date"] else None
    items = []
    for it in out.get("line_items") or []:
        items.append({"description": str(it.get("description", "")).strip(),
                      "quantity": to_num(it.get("quantity")),
                      "unit_price": to_num(it.get("unit_price")),
                      "amount": to_num(it.get("amount"))})
    out["line_items"] = items
    dt = str(out.get("document_type") or "invoice").lower().replace(" ", "_").replace("-", "_")
    out["document_type"] = {"tax_invoice": "invoice", "pro_forma": "proforma", "quote": "quotation"}.get(dt, dt)
    tt = str(out.get("tax_type") or "").upper().replace(" ", "")
    out["tax_type"] = "IGST" if tt == "IGST" else "CGST+SGST" if "CGST" in tt else ("none" if tt == "NONE" else None)
    for k in ("vendor_name", "invoice_number", "po_number", "vendor_gstin", "ifsc", "buyer_gstin"):
        if isinstance(out[k], str):
            out[k] = out[k].strip() or None
    if out.get("bank_account"):
        out["bank_account"] = re.sub(r"\D", "", str(out["bank_account"])) or None
    return out


def extract(file_bytes: bytes, mode: str = "auto") -> tuple[dict, dict]:
    """mode: 'auto' (AI if a key is configured, else local) | 'ai' | 'local'.

    Returns (invoice_data, meta) where meta records method + every attempt.
    """
    attempts = []
    text, pages = pdf_text(file_bytes)
    has_text = len(text.strip()) > 40
    meta = {"pages": pages, "text_layer": has_text, "attempts": attempts}

    if mode in ("auto", "ai"):
        b64 = base64.b64encode(file_bytes).decode()
        for name in available_providers():
            try:
                raw = PROVIDERS[name](b64)
                data = normalise(raw)
                label = PROVIDER_LABEL[name] + (f" · {raw['_model']}" if raw.get("_model") else "")
                attempts.append({"method": label, "ok": True})
                meta["method"] = label
                return data, meta
            except Exception as e:  # noqa: BLE001 — record and fall through to next option
                attempts.append({"method": PROVIDER_LABEL[name], "ok": False, "error": str(e)[:200]})
        if not available_providers():
            attempts.append({"method": "AI extraction", "ok": False, "error": "No API key configured"})

    if has_text:
        data = normalise(heuristic_parse(text))
        meta["method"] = "PDF text layer + rules"
    else:
        try:
            text = ocr_text(file_bytes)
            data = normalise(heuristic_parse(text))
            # OCR is noisier — scale confidence down.
            data["confidence"] = {k: round(v * 0.9, 2) for k, v in data["confidence"].items()}
            meta["method"] = "OCR (Tesseract) + rules"
        except Exception as e:  # noqa: BLE001
            attempts.append({"method": "OCR", "ok": False, "error": str(e)[:200]})
            data = normalise({})
            meta["method"] = "none"
    attempts.append({"method": meta["method"], "ok": meta["method"] != "none"})
    meta["raw_text"] = text[:4000]
    return data, meta
