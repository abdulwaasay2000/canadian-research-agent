"""Point-in-time fundamental and valuation metrics.

Rules
* Only statements with available_date <= as_of are used (no look-ahead).
* Ratios with a meaningless denominator (negative earnings for P/E, negative EBITDA, etc.) are
  NaN with a reason, never a large number that would look 'cheap'.
* Sector-inappropriate metrics are listed in `not_applicable` (e.g. EV/EBITDA for banks).
* Market cap = price x diluted shares; both must be in the same unit system as the statements
  (true for SEC XBRL raw values and for the synthetic dataset, which is in millions).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cra.indicators import annualized_vol, max_drawdown

TAX_RATE_FOR_ROIC = 0.26  # simple statutory approximation; Phase 2 uses reported effective rate

# Metrics that are not meaningful for a sector (excluded from scoring, not penalised).
NOT_APPLICABLE: dict[str, set[str]] = {
    "Financials": {"ev_ebitda", "net_debt_ebitda", "interest_coverage", "current_ratio", "roic",
                   "fcf_conversion", "fcf_margin", "p_fcf", "debt_to_equity"},
    "Real Estate": {"pe", "p_fcf", "fcf_conversion"},  # REITs need FFO/AFFO (Phase 2)
}
# Metrics only meaningful for certain sectors.
ONLY_FOR: dict[str, set[str]] = {"equity_to_assets": {"Financials"}, "p_b": {"Financials", "Real Estate"}}


def _safe_div(a: float, b: float, positive_denominator: bool = True) -> float:
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
        return float("nan")
    if positive_denominator and b < 0:
        return float("nan")
    return float(a) / float(b)


def _cagr(series: pd.Series, years: int) -> float:
    s = series.dropna()
    if len(s) < years + 1:
        return float("nan")
    start, end = s.iloc[-(years + 1)], s.iloc[-1]
    if start <= 0 or end <= 0:
        return float("nan")
    return float((end / start) ** (1 / years) - 1)


def company_metrics(fund: pd.DataFrame, prices: pd.DataFrame, sector: str, as_of: pd.Timestamp) -> dict:
    """fund: PIT long-format fundamentals for one ticker. prices: that ticker's price history."""
    as_of = pd.Timestamp(as_of)
    out: dict = {"as_of": as_of.date().isoformat()}
    fund = fund[pd.to_datetime(fund["available_date"]) <= as_of]
    px = prices[pd.to_datetime(prices["date"]) <= as_of].sort_values("date")
    if px.empty:
        out["price"] = float("nan")
        out["price_date"] = None
    else:
        out["price"] = float(px["close"].iloc[-1])
        out["price_date"] = pd.Timestamp(px["date"].iloc[-1]).date().isoformat()

    if fund.empty:
        out["latest_fy"] = None
        out["not_applicable"] = ""
        return out

    wide = fund.pivot_table(index="fiscal_year", columns="metric", values="value", aggfunc="last").sort_index()
    latest = wide.iloc[-1]
    out["latest_fy"] = int(wide.index[-1])
    out["statements_available_since"] = str(pd.to_datetime(fund["available_date"]).max().date())

    g = lambda k: latest.get(k, np.nan)  # noqa: E731
    rev, ni, op = g("revenue"), g("net_income"), g("operating_income")
    ocf, capex, da = g("operating_cash_flow"), g("capex"), g("depreciation_amortization")
    eq, debt, cash = g("shareholders_equity"), g("long_term_debt"), g("cash")
    shares = g("shares_diluted")
    fcf = ocf - capex if not (pd.isna(ocf) or pd.isna(capex)) else np.nan
    ebitda = op + da if not (pd.isna(op) or pd.isna(da)) else np.nan
    prev_eq = wide["shareholders_equity"].iloc[-2] if "shareholders_equity" in wide and len(wide) > 1 else np.nan
    avg_eq = np.nanmean([eq, prev_eq]) if not pd.isna(eq) else np.nan

    out.update({
        # growth
        "revenue_cagr_3y": _cagr(wide.get("revenue", pd.Series(dtype=float)), 3),
        "revenue_cagr_5y": _cagr(wide.get("revenue", pd.Series(dtype=float)), 5),
        "eps_cagr_3y": _cagr(wide.get("eps_diluted", pd.Series(dtype=float)), 3),
        # profitability
        "operating_margin": _safe_div(op, rev),
        "fcf_margin": _safe_div(fcf, rev),
        "roe": _safe_div(ni, avg_eq),
        "roic": _safe_div(op * (1 - TAX_RATE_FOR_ROIC), (debt if not pd.isna(debt) else 0) + eq
                          - (cash if not pd.isna(cash) else 0)),
        "margin_volatility_5y": float((wide["operating_income"] / wide["revenue"]).tail(5).std())
        if {"operating_income", "revenue"} <= set(wide.columns) and len(wide) >= 5 else np.nan,
        # strength
        "debt_to_equity": _safe_div(debt, eq),
        "net_debt_ebitda": _safe_div((debt if not pd.isna(debt) else np.nan) - (cash if not pd.isna(cash) else 0), ebitda),
        "interest_coverage": _safe_div(op, g("interest_expense")),
        "current_ratio": _safe_div(g("current_assets"), g("current_liabilities")),
        "fcf_conversion": _safe_div(fcf, ni),
        "equity_to_assets": _safe_div(eq, g("total_assets")),
        # dilution
        "share_change_3y": (_safe_div(shares, wide["shares_diluted"].iloc[-4]) - 1)
        if "shares_diluted" in wide and len(wide) >= 4 else np.nan,
    })

    mcap = out["price"] * shares if not pd.isna(shares) else np.nan
    ev = mcap + (debt if not pd.isna(debt) else 0) - (cash if not pd.isna(cash) else 0)
    out.update({
        "market_cap": mcap,
        "pe": _safe_div(mcap, ni),
        "ev_ebitda": _safe_div(ev, ebitda),
        "p_fcf": _safe_div(mcap, fcf),
        "p_s": _safe_div(mcap, rev),
        "p_b": _safe_div(mcap, eq),
    })
    if not pd.isna(ni) and ni <= 0:
        out["notes"] = "Negative earnings: P/E not meaningful."

    # price-based risk (3y volatility, 5y drawdown)
    if len(px) > 252:
        s = px.set_index(pd.to_datetime(px["date"]))["adj_close"]
        out["volatility_3y"] = annualized_vol(s[s.index > as_of - pd.DateOffset(years=3)])
        out["max_drawdown_5y"] = max_drawdown(s[s.index > as_of - pd.DateOffset(years=5)])
    else:
        out["volatility_3y"] = out["max_drawdown_5y"] = np.nan

    # Sector applicability: not-applicable metrics are listed (and blanked), so the scorer
    # removes them from the denominator instead of treating them as missing.
    na = set(NOT_APPLICABLE.get(sector, set()))
    na |= {m for m, sectors in ONLY_FOR.items() if sector not in sectors}
    for m in na:
        out[m] = np.nan
    out["not_applicable"] = ",".join(sorted(na))
    return out
