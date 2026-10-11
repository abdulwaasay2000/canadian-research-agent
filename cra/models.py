"""Shared data contracts. Every dataset carries provenance metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

# Canonical column sets for each dataset kind.
PRICE_COLUMNS = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume", "currency"]
FX_COLUMNS = ["date", "series", "value"]  # value = CAD per 1 unit of foreign currency (BoC convention)
MACRO_COLUMNS = ["date", "series", "value"]
FUNDAMENTAL_COLUMNS = [
    "ticker",
    "metric",
    "period_end",
    "fiscal_year",
    "value",
    "unit",
    "available_date",  # first date the number was public (filing date). Used to block look-ahead.
    "form",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Provenance:
    source: str  # e.g. "bank_of_canada_valet", "sec_edgar", "synthetic"
    retrieved_at: datetime = field(default_factory=utcnow)
    url: str | None = None
    is_synthetic: bool = False
    is_realtime: bool = False  # Phase 1 sources are end-of-day / delayed. Never claim real-time.
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "retrieved_at": self.retrieved_at.isoformat(),
            "url": self.url,
            "is_synthetic": self.is_synthetic,
            "is_realtime": self.is_realtime,
            "notes": self.notes,
        }


@dataclass
class Dataset:
    kind: str  # "prices" | "fx" | "macro" | "fundamentals"
    frame: pd.DataFrame
    provenance: Provenance

    def __post_init__(self) -> None:
        expected = {
            "prices": PRICE_COLUMNS,
            "fx": FX_COLUMNS,
            "macro": MACRO_COLUMNS,
            "fundamentals": FUNDAMENTAL_COLUMNS,
        }.get(self.kind)
        if expected is None:
            raise ValueError(f"Unknown dataset kind {self.kind!r}")
        missing = [c for c in expected if c not in self.frame.columns]
        if missing:
            raise ValueError(f"{self.kind} dataset missing columns: {missing}")
        self.frame = self.frame[expected].copy()
