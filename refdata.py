"""Reference data: vendor master, purchase orders and PO lines (the 'procurement system')."""
from functools import lru_cache

import pandas as pd

from .config import REF_DIR


@lru_cache(maxsize=1)
def vendors() -> pd.DataFrame:
    return pd.read_csv(REF_DIR / "vendors.csv", dtype=str)


@lru_cache(maxsize=1)
def purchase_orders() -> pd.DataFrame:
    df = pd.read_csv(REF_DIR / "purchase_orders.csv", dtype={"po_number": str, "vendor_id": str})
    df["po_amount_pre_tax"] = df["po_amount_pre_tax"].astype(float)
    return df


@lru_cache(maxsize=1)
def po_lines() -> pd.DataFrame:
    df = pd.read_csv(REF_DIR / "po_lines.csv", dtype={"po_number": str})
    df["quantity"] = df["quantity"].astype(float)
    df["unit_price"] = df["unit_price"].astype(float)
    return df


def vendor_by_id(vendor_id: str) -> dict | None:
    v = vendors()
    row = v[v["vendor_id"] == vendor_id]
    return None if row.empty else row.iloc[0].to_dict()


def po_by_number(po_number: str) -> dict | None:
    p = purchase_orders()
    row = p[p["po_number"].str.upper() == po_number.upper()]
    return None if row.empty else row.iloc[0].to_dict()


def lines_for_po(po_number: str) -> list[dict]:
    return po_lines()[po_lines()["po_number"] == po_number].to_dict("records")
