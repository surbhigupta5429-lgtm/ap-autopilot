"""Run history + PO ledger, stored in SQLite.

Every run is persisted with its full stage trace, so the dashboard can replay
exactly what the process saw and decided. The PO ledger (what has already been
billed against each PO) is derived from approved runs; that is what makes
split-invoice detection work.
"""
import json
import sqlite3

from config import DB_PATH, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    file_name TEXT,
    file_hash TEXT,
    file_bytes BLOB,
    extraction_method TEXT,
    vendor_id TEXT,
    vendor_name TEXT,
    invoice_number TEXT,
    invoice_number_norm TEXT,
    invoice_date TEXT,
    po_number TEXT,
    pre_tax REAL,
    tax REAL,
    total REAL,
    decision TEXT,          -- APPROVE / REVIEW / REJECT  (what the system decided)
    status TEXT,            -- approved / pending_review / rejected / approved_by_reviewer / rejected_by_reviewer
    headline TEXT,
    findings TEXT,          -- JSON list
    stages TEXT,            -- JSON list (full trace)
    extracted TEXT,         -- JSON
    duration_ms INTEGER,
    reviewer_note TEXT,
    reviewed_at TEXT
);
"""

# Statuses that count as "money committed" against a PO / block a duplicate.
COMMITTED = ("approved", "approved_by_reviewer")
LIVE = ("approved", "approved_by_reviewer", "pending_review")


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    return con


def save_run(run: dict, file_bytes: bytes):
    con = connect()
    with con:
        con.execute(
            """INSERT INTO runs VALUES (:id,:created_at,:file_name,:file_hash,:file_bytes,:extraction_method,
            :vendor_id,:vendor_name,:invoice_number,:invoice_number_norm,:invoice_date,:po_number,:pre_tax,:tax,
            :total,:decision,:status,:headline,:findings,:stages,:extracted,:duration_ms,NULL,NULL)""",
            {**run, "file_bytes": file_bytes,
             "findings": json.dumps(run["findings"], default=str),
             "stages": json.dumps(run["stages"], default=str),
             "extracted": json.dumps(run["extracted"], default=str)},
        )
    con.close()


def list_runs() -> list[dict]:
    con = connect()
    rows = con.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("findings", "stages", "extracted"):
            d[k] = json.loads(d[k]) if d[k] else []
        out.append(d)
    return out


def get_run(run_id: str) -> dict | None:
    return next((r for r in list_runs() if r["id"] == run_id), None)


def review_run(run_id: str, approve: bool, note: str):
    con = connect()
    with con:
        con.execute(
            "UPDATE runs SET status=?, reviewer_note=?, reviewed_at=? WHERE id=?",
            ("approved_by_reviewer" if approve else "rejected_by_reviewer", note,
             now().isoformat(timespec="seconds"), run_id),
        )
    con.close()


def billed_against_po(po_number: str) -> list[dict]:
    """Invoices already committed against a PO (approved, by system or reviewer)."""
    con = connect()
    rows = con.execute(
        f"SELECT id, invoice_number, pre_tax, created_at, status FROM runs "
        f"WHERE po_number=? AND status IN ({','.join('?' * len(COMMITTED))}) ORDER BY created_at",
        (po_number, *COMMITTED),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def live_invoices_for_vendor(vendor_id: str) -> list[dict]:
    """Invoices from this vendor that are approved or awaiting review (candidates for duplicates)."""
    con = connect()
    rows = con.execute(
        f"SELECT id, invoice_number, invoice_number_norm, invoice_date, total, file_hash, created_at, status "
        f"FROM runs WHERE vendor_id=? AND status IN ({','.join('?' * len(LIVE))})",
        (vendor_id, *LIVE),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def file_seen(file_hash: str) -> dict | None:
    con = connect()
    r = con.execute("SELECT id, file_name, created_at, status FROM runs WHERE file_hash=? ORDER BY created_at LIMIT 1",
                    (file_hash,)).fetchone()
    con.close()
    return dict(r) if r else None


def reset():
    con = connect()
    with con:
        con.execute("DELETE FROM runs")
    con.close()
