"""Trackers: one consistent price-change view across stocks, ETFs, FX pairs and rates,
plus rule-based research alerts (unusual moves, 52-week extremes, stale data).

Alerts are observations, not instructions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cra.pairs import build_pairs


def _change(s: pd.Series, days: int) -> float:
    s = s.dropna()
    if s.empty:
        return np.nan
    past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
    return float(s.iloc[-1] / past.iloc[-1] - 1) if len(past) else np.nan


def _ytd(s: pd.Series) -> float:
    s = s.dropna()
    if s.empty:
        return np.nan
    prior = s[s.index < pd.Timestamp(year=s.index[-1].year, month=1, day=1)]
    return float(s.iloc[-1] / prior.iloc[-1] - 1) if len(prior) else np.nan


def series_stats(s: pd.Series) -> dict:
    """Common tracker fields for any price-like series indexed by date."""
    s = s.dropna().sort_index()
    if s.empty:
        return {"last": np.nan, "as_of": None}
    rets = np.log(s / s.shift()).dropna()
    last_year = s[s.index > s.index[-1] - pd.Timedelta(days=365)]
    sigma = float(rets.tail(252).std()) if len(rets) > 20 else np.nan
    last_ret = float(rets.iloc[-1]) if len(rets) else np.nan
    return {
        "last": float(s.iloc[-1]),
        "as_of": s.index[-1].date().isoformat(),
        "chg_1d": float(s.iloc[-1] / s.iloc[-2] - 1) if len(s) > 1 else np.nan,
        "chg_1w": _change(s, 7),
        "chg_1m": _change(s, 30),
        "chg_3m": _change(s, 91),
        "chg_ytd": _ytd(s),
        "chg_1y": _change(s, 365),
        "high_52w": float(last_year.max()),
        "low_52w": float(last_year.min()),
        "pct_from_52w_high": float(s.iloc[-1] / last_year.max() - 1),
        "move_sigma": last_ret / sigma if sigma and not np.isnan(sigma) and sigma > 0 else np.nan,
        "spark": [round(float(v), 6) for v in s.tail(90).to_numpy()[::3]],
    }


def asset_trackers(wh) -> pd.DataFrame:
    uni = wh.universe()
    rows = []
    for r in uni.itertuples():
        px = wh.prices(r.ticker)
        if px.empty:
            rows.append({"ticker": r.ticker, "name": r.name, "status": "no data"})
            continue
        s = px.set_index(pd.to_datetime(px["date"]))["adj_close"]
        rows.append({"ticker": r.ticker, "name": r.name, "exchange": r.exchange, "country": r.country,
                     "sector": r.sector, "asset_type": r.asset_type, "currency": r.currency, "status": "ok",
                     "source": px["source"].iloc[-1], "is_synthetic": bool(px["is_synthetic"].iloc[-1]),
                     **series_stats(s)})
    return pd.DataFrame(rows)


def fx_trackers(fx_long: pd.DataFrame) -> pd.DataFrame:
    wide = build_pairs(fx_long)
    synthetic = bool(fx_long["is_synthetic"].any()) if "is_synthetic" in fx_long else None
    source = fx_long["source"].iloc[-1] if "source" in fx_long and len(fx_long) else None
    rows = [{"pair": p, "source": source, "is_synthetic": synthetic, **series_stats(wide[p])} for p in wide.columns]
    return pd.DataFrame(rows)


def rate_trackers(macro: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sid, g in macro.groupby("series"):
        s = g.set_index(pd.to_datetime(g["date"]))["value"].sort_index()
        last = s.iloc[-1]
        def diff(days):
            past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
            return float(last - past.iloc[-1]) if len(past) else np.nan
        rows.append({"series": sid, "last": float(last), "as_of": s.index[-1].date().isoformat(),
                     "chg_1m_pts": diff(30), "chg_1y_pts": diff(365), "source": g["source"].iloc[-1],
                     "is_synthetic": bool(g["is_synthetic"].any())})
    return pd.DataFrame(rows)


def alerts(assets: pd.DataFrame, fx: pd.DataFrame, as_of: pd.Timestamp | None = None,
           sigma_threshold: float = 2.5, stale_bdays: int = 3) -> pd.DataFrame:
    out = []
    frames = [(assets.rename(columns={"ticker": "id"}), "asset"), (fx.rename(columns={"pair": "id"}), "fx")]
    ref = as_of
    if ref is None:
        dates = pd.to_datetime(pd.concat([assets.get("as_of", pd.Series(dtype=str)), fx.get("as_of", pd.Series(dtype=str))]).dropna())
        ref = dates.max() if len(dates) else pd.Timestamp.today()
    for df, kind in frames:
        for r in df.to_dict("records"):
            if r.get("status") == "no data":
                out.append({"id": r["id"], "kind": kind, "severity": "high", "alert": "No data loaded"})
                continue
            ms = r.get("move_sigma")
            if ms is not None and not pd.isna(ms) and abs(ms) >= sigma_threshold:
                out.append({"id": r["id"], "kind": kind, "severity": "medium",
                            "alert": f"Unusual 1-day move: {r['chg_1d']:+.2%} ({ms:+.1f} sigma)"})
            if r.get("pct_from_52w_high") is not None and not pd.isna(r.get("pct_from_52w_high")):
                if r["pct_from_52w_high"] >= -0.005:
                    out.append({"id": r["id"], "kind": kind, "severity": "info", "alert": "At/near 52-week high"})
                elif r["last"] <= r["low_52w"] * 1.005:
                    out.append({"id": r["id"], "kind": kind, "severity": "info", "alert": "At/near 52-week low"})
            if r.get("as_of"):
                lag = int(np.busday_count(pd.Timestamp(r["as_of"]).date(), pd.Timestamp(ref).date()))
                if lag > stale_bdays:
                    out.append({"id": r["id"], "kind": kind, "severity": "high",
                                "alert": f"Stale data: last observation {r['as_of']} ({lag} business days old)"})
    return pd.DataFrame(out, columns=["id", "kind", "severity", "alert"])
