"""Exploratory technical indicators and basic return statistics.

These describe past price behaviour. They are NOT evidence of predictive power; Phase 3
tests whether any signal survives costs and regime changes out of sample.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd})


def log_returns(s: pd.Series) -> pd.Series:
    return np.log(s / s.shift())


def annualized_vol(s: pd.Series, window: int | None = None) -> float | pd.Series:
    r = log_returns(s)
    if window:
        return r.rolling(window).std() * np.sqrt(TRADING_DAYS)
    return float(r.std() * np.sqrt(TRADING_DAYS))


def max_drawdown(s: pd.Series) -> float:
    s = s.dropna()
    if s.empty:
        return float("nan")
    return float((s / s.cummax() - 1).min())


def cagr(s: pd.Series) -> float:
    s = s.dropna()
    if len(s) < 2:
        return float("nan")
    years = (s.index[-1] - s.index[0]).days / 365.25
    return float((s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1) if years > 0 else float("nan")


def trend_state(s: pd.Series) -> str:
    """Descriptive label from moving-average alignment. Returns 'insufficient data' when
    fewer than 200 observations are available."""
    s = s.dropna()
    if len(s) < 200:
        return "insufficient data"
    p, m50, m200 = s.iloc[-1], sma(s, 50).iloc[-1], sma(s, 200).iloc[-1]
    if p > m50 > m200:
        return "uptrend"
    if p < m50 < m200:
        return "downtrend"
    return "mixed / range"
