"""Canadian / US screener and ETF view built from the warehouse."""

from __future__ import annotations

import pandas as pd

from cra.indicators import annualized_vol, cagr, max_drawdown
from cra.pairs import usd_to_cad
from cra.metrics import company_metrics
from cra.scoring import load_config, score_universe
from cra.store import Warehouse

MARKETS = {"CA": {"TSX", "TSXV"}, "US": {"NYSE", "NASDAQ", "NYSEARCA"}}


def run_stock_screen(wh: Warehouse, market: str, as_of: str | pd.Timestamp | None = None,
                     config: dict | None = None) -> pd.DataFrame:
    uni = wh.universe()
    uni = uni[(uni["exchange"].isin(MARKETS[market])) & (uni["asset_type"] == "stock")]
    if uni.empty:
        return pd.DataFrame()
    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp(
        wh.query("SELECT max(date) d FROM prices")["d"].iloc[0])
    fund_all = wh.fundamentals(as_of=as_of)
    rows = []
    for r in uni.itertuples():
        m = company_metrics(fund_all[fund_all["ticker"] == r.ticker], wh.prices(r.ticker), r.sector, as_of)
        src = wh.query("SELECT any_value(source) s, bool_or(is_synthetic) syn FROM prices WHERE ticker=?", [r.ticker])
        m.update({"ticker": r.ticker, "name": r.name, "exchange": r.exchange, "sector": r.sector,
                  "currency": r.currency, "price_source": src["s"].iloc[0],
                  "is_synthetic": bool(src["syn"].iloc[0]) if pd.notna(src["syn"].iloc[0]) else None})
        rows.append(m)
    metrics = pd.DataFrame(rows)
    scored = score_universe(metrics, config or load_config())
    if market == "US":
        fx = wh.fx(["FXUSDCAD"])
        if not fx.empty:
            tmp = scored[["ticker", "price_date", "price"]].copy()
            tmp["price_date"] = pd.to_datetime(tmp["price_date"])
            conv = usd_to_cad(tmp, fx, ["price"], date_col="price_date")
            scored = scored.merge(conv[["ticker", "price_cad", "usdcad"]], on="ticker", how="left")
    return scored


def run_etf_view(wh: Warehouse, as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    uni = wh.universe()
    uni = uni[uni["asset_type"] == "etf"]
    rows = []
    for r in uni.itertuples():
        px = wh.prices(r.ticker)
        if px.empty:
            continue
        s = px.set_index(pd.to_datetime(px["date"]))["adj_close"]
        if as_of is not None:
            s = s[s.index <= pd.Timestamp(as_of)]
        five = s[s.index > s.index[-1] - pd.DateOffset(years=5)]
        rows.append({"ticker": r.ticker, "name": r.name, "exchange": r.exchange, "category": r.industry,
                     "currency": r.currency, "price": float(s.iloc[-1]), "price_date": s.index[-1].date().isoformat(),
                     "cagr_5y": cagr(five), "volatility_5y": annualized_vol(five),
                     "max_drawdown_5y": max_drawdown(five), "source": px["source"].iloc[-1],
                     "is_synthetic": bool(px["is_synthetic"].iloc[-1])})
    return pd.DataFrame(rows)
