"""Deterministic SYNTHETIC demonstration data.

Every company here is fictional (tickers start with 'Z' and names say 'DEMO'), every row is
flagged is_synthetic=True, and the dashboard shows a banner whenever synthetic data is in use.
Synthetic data exists only so the pipeline, tests and dashboard run without paid data. It says
nothing about any real security.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from cra.models import FUNDAMENTAL_COLUMNS, PRICE_COLUMNS, Dataset, Provenance

SYNTH_NOTE = "SYNTHETIC DEMO DATA - fictional, not market data."


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


def load_universe(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["is_synthetic"] = df["is_synthetic"].astype(str).str.lower().isin({"true", "1", "yes"})
    return df


def business_days(start: str, end: str) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end)


def synthetic_prices(universe: pd.DataFrame, start: str = "2014-01-01", end: str = "2026-09-30") -> Dataset:
    days = business_days(start, end)
    frames = []
    for row in universe.itertuples():
        rng = np.random.default_rng(_seed("px", row.ticker))
        n = len(days)
        drift = rng.uniform(0.02, 0.10) / 252
        if row.asset_type == "etf":
            base_vol = (0.06 if "Bond" in row.industry else 0.15) / np.sqrt(252)
            drift = (0.025 if "Bond" in row.industry else 0.07) / 252
        else:
            base_vol = rng.uniform(0.18, 0.40) / np.sqrt(252)
        # Regime-switching volatility (calm / stressed) to make drawdowns realistic.
        regime = (rng.random(n) < 0.01).cumsum() % 2
        vol = np.where(regime == 1, base_vol * 1.8, base_vol)
        rets = np.clip(drift + vol * rng.standard_t(df=4, size=n) / np.sqrt(2), -0.25, 0.25)
        close = 20 * rng.uniform(0.5, 5) * np.exp(np.cumsum(rets))
        intraday = np.abs(rng.normal(0, vol, n)) * close
        open_ = close * (1 + rng.normal(0, vol / 3, n))
        high = np.maximum(open_, close) + intraday / 2
        low = np.maximum(np.minimum(open_, close) - intraday / 2, 0.01)
        frames.append(pd.DataFrame({
            "date": days, "ticker": row.ticker, "open": open_.round(4), "high": high.round(4),
            "low": low.round(4), "close": close.round(4), "adj_close": close.round(4),
            "volume": rng.integers(5e4, 5e6, n), "currency": row.currency,
        }))
    df = pd.concat(frames, ignore_index=True)[PRICE_COLUMNS]
    return Dataset("prices", df, Provenance(source="synthetic", is_synthetic=True, notes=SYNTH_NOTE))


FX_LEVELS = {"FXUSDCAD": 1.36, "FXEURCAD": 1.48, "FXGBPCAD": 1.72, "FXJPYCAD": 0.0092, "FXAUDCAD": 0.90}


def synthetic_fx(start: str = "2017-01-03", end: str = "2026-09-30") -> Dataset:
    days = business_days(start, end)
    frames = []
    for series, level in FX_LEVELS.items():
        rng = np.random.default_rng(_seed("fx", series))
        # Mean-reverting log level (OU) so rates stay in a plausible band.
        x = np.zeros(len(days))
        for i in range(1, len(days)):
            x[i] = x[i - 1] * 0.998 + rng.normal(0, 0.0045)
        frames.append(pd.DataFrame({"date": days, "series": series, "value": (level * np.exp(x)).round(6)}))
    return Dataset("fx", pd.concat(frames, ignore_index=True),
                   Provenance(source="synthetic", is_synthetic=True, notes=SYNTH_NOTE))


def synthetic_macro(start: str = "2017-01-03", end: str = "2026-09-30") -> Dataset:
    days = business_days(start, end)
    specs = {"V39079": 2.75, "BD.CDN.2YR.DQ.YLD": 2.7, "BD.CDN.10YR.DQ.YLD": 3.1,
             "DFF": 4.1, "DGS2": 3.6, "DGS10": 4.1}
    frames = []
    for s, lvl in specs.items():
        rng = np.random.default_rng(_seed("macro", s))
        steps = rng.choice([-0.25, 0, 0, 0, 0, 0.25], size=len(days) // 30 + 1) if s in {"V39079", "DFF"} \
            else rng.normal(0, 0.15, len(days) // 30 + 1)
        path = np.clip(lvl + np.cumsum(steps) * 0.3, 0.25, 7)
        frames.append(pd.DataFrame({"date": days, "series": s, "value": np.repeat(path, 30)[: len(days)].round(3)}))
    return Dataset("macro", pd.concat(frames, ignore_index=True),
                   Provenance(source="synthetic", is_synthetic=True, notes=SYNTH_NOTE))


def synthetic_fundamentals(universe: pd.DataFrame, first_fy: int = 2015, last_fy: int = 2025) -> Dataset:
    rows = []
    for row in universe[universe["asset_type"] == "stock"].itertuples():
        rng = np.random.default_rng(_seed("fund", row.ticker))
        revenue = rng.uniform(500, 20000)  # millions
        growth = rng.uniform(-0.02, 0.15)
        margin = rng.uniform(0.03, 0.30)
        leverage = rng.uniform(0.1, 2.5) if row.sector != "Financials" else rng.uniform(8, 14)
        shares = rng.uniform(100, 2000)
        # Some companies intentionally have gaps so missing-data handling is exercised.
        missing_metric = rng.choice(["none", "capex", "interest_expense"], p=[0.7, 0.15, 0.15])
        for fy in range(first_fy, last_fy + 1):
            revenue *= 1 + growth + rng.normal(0, 0.05)
            m = float(np.clip(margin + rng.normal(0, 0.03), -0.1, 0.5))
            op_inc = revenue * m
            net_inc = op_inc * 0.74
            equity = revenue * rng.uniform(0.4, 1.2)
            debt = equity * leverage * (0.6 if row.sector != "Financials" else 1)
            values = {
                "revenue": revenue, "operating_income": op_inc, "net_income": net_inc,
                "eps_diluted": net_inc / shares, "operating_cash_flow": net_inc * rng.uniform(0.9, 1.4),
                "capex": revenue * rng.uniform(0.02, 0.12),
                "depreciation_amortization": revenue * rng.uniform(0.02, 0.08),
                "total_assets": equity + debt * 1.3, "total_liabilities": debt * 1.3,
                "shareholders_equity": equity, "current_assets": revenue * 0.4,
                "current_liabilities": revenue * rng.uniform(0.2, 0.45), "cash": revenue * rng.uniform(0.03, 0.2),
                "long_term_debt": debt, "interest_expense": debt * rng.uniform(0.03, 0.06),
                "shares_diluted": shares,
            }
            period_end = pd.Timestamp(f"{fy}-12-31")
            available = period_end + pd.Timedelta(days=int(rng.integers(45, 90)))
            for metric, val in values.items():
                if metric == missing_metric:
                    continue
                unit = "shares" if metric == "shares_diluted" else ("per_share" if metric == "eps_diluted" else "millions")
                rows.append({"ticker": row.ticker, "metric": metric, "period_end": period_end,
                             "fiscal_year": fy, "value": round(float(val), 4), "unit": unit,
                             "available_date": available, "form": "SYNTH-ANNUAL"})
    df = pd.DataFrame(rows, columns=FUNDAMENTAL_COLUMNS)
    return Dataset("fundamentals", df, Provenance(source="synthetic", is_synthetic=True, notes=SYNTH_NOTE))


def anchor_prices_to_fundamentals(prices: Dataset, fundamentals: Dataset) -> Dataset:
    """Rescale each synthetic stock's price path so its latest valuation is plausible
    (P/E roughly 8-30x on the last reported earnings). Only the level changes; returns,
    volatility and drawdowns are untouched."""
    px, f = prices.frame.copy(), fundamentals.frame
    for ticker, g in f.groupby("ticker"):
        last_fy = g["fiscal_year"].max()
        latest = g[g["fiscal_year"] == last_fy].set_index("metric")["value"]
        ni, shares = latest.get("net_income"), latest.get("shares_diluted")
        if ni is None or shares is None or ni <= 0:
            continue
        rng = np.random.default_rng(_seed("pe", ticker))
        target_price = ni * rng.uniform(8, 30) / shares
        mask = px["ticker"] == ticker
        factor = target_price / px.loc[mask, "close"].iloc[-1]
        for c in ["open", "high", "low", "close", "adj_close"]:
            px.loc[mask, c] = (px.loc[mask, c] * factor).round(4)
    return Dataset("prices", px, prices.provenance)
