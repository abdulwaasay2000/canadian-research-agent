"""Import licensed data you already have (broker exports, a data vendor download, or numbers
transcribed from SEDAR+ filings) from CSV files that follow the canonical schemas.

This is the Phase 1 path for Canadian fundamentals: SEDAR+ has no public bulk API, so
Canadian statements come from either a licensed vendor or curated CSVs.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from cra.http import DataSourceError
from cra.models import FUNDAMENTAL_COLUMNS, PRICE_COLUMNS, Dataset, Provenance

SCHEMAS = {"prices": PRICE_COLUMNS, "fundamentals": FUNDAMENTAL_COLUMNS,
           "fx": ["date", "series", "value"], "macro": ["date", "series", "value"]}
DATE_COLS = {"date", "period_end", "available_date"}


def import_csv(path: str | Path, kind: str, source_name: str) -> Dataset:
    path = Path(path)
    if kind not in SCHEMAS:
        raise DataSourceError(f"Unknown kind {kind}")
    df = pd.read_csv(path)
    missing = [c for c in SCHEMAS[kind] if c not in df.columns]
    if missing:
        raise DataSourceError(f"{path.name}: missing required columns {missing}")
    for c in DATE_COLS & set(df.columns):
        df[c] = pd.to_datetime(df[c])
    if kind == "fundamentals" and df["available_date"].isna().any():
        raise DataSourceError(
            f"{path.name}: every fundamental row needs available_date (public release date) "
            "to prevent look-ahead bias")
    return Dataset(kind, df, Provenance(source=source_name, url=str(path), notes="User-supplied CSV import"))
