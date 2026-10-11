"""Build the Phase 1 forex pair universe from Bank of Canada CAD-based series and convert
USD amounts to CAD point-in-time.

All seven pairs are derived from BoC daily indicative rates (CAD per unit of foreign currency).
Non-CAD crosses (EUR/USD, GBP/USD, USD/JPY) are *computed* crosses of two indicative rates.
They are good for research context but are not tradable quotes and carry no bid/ask spread;
spread-aware research needs a licensed bid/ask feed (see docs/DATA_SOURCES.md).
"""

from __future__ import annotations

import pandas as pd

# pair -> (numerator series, denominator series or None)
PAIRS: dict[str, tuple[str, str | None]] = {
    "USD/CAD": ("FXUSDCAD", None),
    "EUR/USD": ("FXEURCAD", "FXUSDCAD"),
    "GBP/USD": ("FXGBPCAD", "FXUSDCAD"),
    "USD/JPY": ("FXUSDCAD", "FXJPYCAD"),
    "EUR/CAD": ("FXEURCAD", None),
    "GBP/CAD": ("FXGBPCAD", None),
    "AUD/CAD": ("FXAUDCAD", None),
}


def build_pairs(fx_long: pd.DataFrame) -> pd.DataFrame:
    """Return a wide frame indexed by date with one column per pair. Dates where any required
    leg is missing stay NaN (no forward filling)."""
    if fx_long.empty:
        return pd.DataFrame(columns=list(PAIRS))
    wide = fx_long.pivot_table(index="date", columns="series", values="value", aggfunc="last").sort_index()
    out = pd.DataFrame(index=wide.index)
    for pair, (num, den) in PAIRS.items():
        if num not in wide:
            continue
        if den is None:
            out[pair] = wide[num]
        elif den in wide:
            out[pair] = wide[num] / wide[den]
    out.index = pd.to_datetime(out.index)
    return out


def usd_to_cad(df: pd.DataFrame, fx_long: pd.DataFrame, amount_cols: list[str], date_col: str = "date",
               lag_days: int = 0, max_staleness_days: int = 7) -> pd.DataFrame:
    """Convert USD columns to CAD using the latest BoC USD/CAD rate on or before (date - lag_days).

    lag_days=0 is fine for reporting. Use lag_days=1 in backtests: the BoC rate for day t is
    published ~16:30 ET, after the 16:00 equity close, so same-day use would leak information.
    Rates older than max_staleness_days are rejected (result left NaN) instead of being reused.
    """
    usd = (fx_long[fx_long["series"] == "FXUSDCAD"][["date", "value"]]
           .rename(columns={"date": "fx_date", "value": "usdcad"}).sort_values("fx_date"))
    # Normalise both keys to ns resolution (DuckDB/pandas versions differ: us vs ns).
    usd["fx_date"] = pd.to_datetime(usd["fx_date"]).astype("datetime64[ns]")
    left = df.copy()
    left["_lookup"] = (pd.to_datetime(left[date_col]) - pd.Timedelta(days=lag_days)).astype("datetime64[ns]")
    left = left.sort_values("_lookup")
    merged = pd.merge_asof(left, usd, left_on="_lookup", right_on="fx_date", direction="backward")
    stale = (merged["_lookup"] - merged["fx_date"]).dt.days > max_staleness_days
    merged.loc[stale, "usdcad"] = float("nan")
    for c in amount_cols:
        merged[f"{c}_cad"] = merged[c] * merged["usdcad"]
    return merged.drop(columns=["_lookup"]).sort_index()
