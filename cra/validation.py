"""Data-quality checks. Each check returns structured issues; nothing is silently 'fixed'."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Issue:
    severity: str  # "error" | "warning" | "info"
    check: str
    subject: str
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


def validate_prices(df: pd.DataFrame, max_gap_bdays: int = 5, max_abs_return: float = 0.5) -> list[Issue]:
    issues: list[Issue] = []
    if df.empty:
        return [Issue("error", "empty", "prices", "No price rows")]
    for ticker, g in df.groupby("ticker"):
        g = g.sort_values("date")
        if g["date"].duplicated().any():
            issues.append(Issue("error", "duplicate_dates", ticker, f"{int(g['date'].duplicated().sum())} duplicates"))
        nulls = g[["open", "high", "low", "close"]].isna().sum().sum()
        if nulls:
            issues.append(Issue("warning", "null_ohlc", ticker, f"{int(nulls)} null OHLC values"))
        if (g[["open", "high", "low", "close"]] <= 0).any().any():
            issues.append(Issue("error", "non_positive_price", ticker, "Price <= 0 found"))
        bad_hl = (g["high"] < g[["open", "close", "low"]].max(axis=1) - 1e-9) | \
                 (g["low"] > g[["open", "close", "high"]].min(axis=1) + 1e-9)
        if bad_hl.any():
            issues.append(Issue("error", "ohlc_inconsistent", ticker, f"{int(bad_hl.sum())} rows high<low/open/close"))
        if (g["volume"] < 0).any():
            issues.append(Issue("error", "negative_volume", ticker, "Negative volume"))
        rets = g["adj_close"].pct_change().abs()
        jumps = rets[rets > max_abs_return]
        if len(jumps):
            issues.append(Issue("warning", "extreme_return", ticker,
                                f"{len(jumps)} daily moves >{max_abs_return:.0%} - check splits/corporate actions"))
        gaps = np.busday_count(g["date"].values[:-1].astype("datetime64[D]"),
                               g["date"].values[1:].astype("datetime64[D]"))
        if len(gaps) and gaps.max() > max_gap_bdays:
            issues.append(Issue("warning", "date_gap", ticker, f"Largest gap {int(gaps.max())} business days"))
    return issues


def validate_series(df: pd.DataFrame, max_abs_change: float = 0.1) -> list[Issue]:
    """For FX/macro long-format [date, series, value]."""
    issues: list[Issue] = []
    if df.empty:
        return [Issue("error", "empty", "series", "No observations")]
    for s, g in df.groupby("series"):
        g = g.sort_values("date")
        if g["date"].duplicated().any():
            issues.append(Issue("error", "duplicate_dates", s, "Duplicate dates"))
        if (g["value"] <= 0).any() and s.startswith("FX"):
            issues.append(Issue("error", "non_positive_fx", s, "FX rate <= 0"))
        if s.startswith("FX"):
            ch = g["value"].pct_change().abs()
            if (ch > max_abs_change).any():
                issues.append(Issue("warning", "fx_jump", s, f"Daily FX change >{max_abs_change:.0%}"))
    return issues


def check_staleness(last_date: pd.Timestamp | None, as_of: pd.Timestamp, max_bdays: int = 3,
                    subject: str = "") -> Issue | None:
    if last_date is None or pd.isna(last_date):
        return Issue("error", "no_data", subject, "No data loaded")
    lag = int(np.busday_count(last_date.date(), as_of.date()))
    if lag > max_bdays:
        return Issue("warning", "stale", subject, f"Last observation {last_date.date()} is {lag} business days old")
    return None


def validate_fundamentals(df: pd.DataFrame) -> list[Issue]:
    issues: list[Issue] = []
    if df.empty:
        return [Issue("warning", "empty", "fundamentals", "No fundamental rows")]
    leak = df[df["available_date"] < df["period_end"]]
    if len(leak):
        issues.append(Issue("error", "available_before_period_end", "fundamentals",
                            f"{len(leak)} rows claim availability before period end"))
    return issues
