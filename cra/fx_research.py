"""Phase 1 forex research summary: descriptive statistics only.

The trend label is descriptive (where price sits versus its moving averages). It is not a
forecast. Bullish/bearish research calls with uncertainty bands arrive in Phase 2/3, after
signals are tested net of spreads, financing and regime changes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cra import indicators as ind
from cra.pairs import PAIRS, build_pairs


def pair_summary(fx_long: pd.DataFrame) -> pd.DataFrame:
    wide = build_pairs(fx_long)
    rows = []
    for pair in PAIRS:
        if pair not in wide:
            rows.append({"pair": pair, "status": "no data"})
            continue
        s = wide[pair].dropna()
        if s.empty:
            rows.append({"pair": pair, "status": "no data"})
            continue
        last = s.index[-1]
        def chg(days: int) -> float:
            past = s[s.index <= last - pd.Timedelta(days=days)]
            return float(s.iloc[-1] / past.iloc[-1] - 1) if len(past) else np.nan
        rows.append({
            "pair": pair, "status": "ok", "last": float(s.iloc[-1]), "as_of": last.date().isoformat(),
            "chg_1w": chg(7), "chg_1m": chg(30), "chg_1y": chg(365),
            "vol_3m_ann": float(ind.log_returns(s).tail(63).std() * np.sqrt(252)),
            "vol_1y_ann": float(ind.log_returns(s).tail(252).std() * np.sqrt(252)),
            "rsi_14": float(ind.rsi(s).iloc[-1]) if len(s) > 15 else np.nan,
            "trend_state": ind.trend_state(s),
            "sma_50": float(ind.sma(s, 50).iloc[-1]) if len(s) >= 50 else np.nan,
            "sma_200": float(ind.sma(s, 200).iloc[-1]) if len(s) >= 200 else np.nan,
        })
    return pd.DataFrame(rows)


def pair_history(fx_long: pd.DataFrame, pair: str) -> pd.DataFrame:
    s = build_pairs(fx_long)[pair].dropna()
    out = pd.DataFrame({"close": s})
    out["sma_20"], out["sma_50"], out["sma_200"] = ind.sma(s, 20), ind.sma(s, 50), ind.sma(s, 200)
    out = out.join(ind.bollinger(s)).join(ind.macd(s))
    out["rsi_14"] = ind.rsi(s)
    return out
