"""Decision matrix: turns tracker + screener outputs into one transparent grid per asset.

Each factor scores +1 / 0 / -1, or None when there is no data (None is never counted as 0).
The verdict is a research label:
  stocks & ETFs -> WATCH / RESEARCH FURTHER / AVOID FOR NOW / INSUFFICIENT DATA
  forex         -> BULLISH / BEARISH / NEUTRAL / INSUFFICIENT EVIDENCE
Every row says which factors drove it and that the rules are not yet backtested.
Nothing here is a trade instruction.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from cra import indicators as ind
from cra.pairs import build_pairs

DEFAULT_CONFIG = Path(__file__).resolve().parent / "decision_matrix.json"
VALIDATION_NOTE = "Rules not yet backtested (Phase 3). Treat as research triage."

STOCK_FACTORS = ["quality", "valuation", "growth", "trend", "momentum", "risk"]
ETF_FACTORS = ["trend", "momentum", "risk_adj_return", "drawdown"]
FX_FACTORS = ["trend", "momentum", "rate_differential", "stretch"]


def load_config(path: str | Path | None = None) -> dict:
    return json.loads(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and np.isnan(x))


def _band(x, pos, neg, higher_is_better=True):
    """Map a number to +1/0/-1 using two thresholds."""
    if _isnan(x):
        return None
    if higher_is_better:
        return 1 if x >= pos else (-1 if x <= neg else 0)
    return 1 if x <= pos else (-1 if x >= neg else 0)


def trend_factor(s: pd.Series):
    state = ind.trend_state(s)
    return {"uptrend": 1, "downtrend": -1, "mixed / range": 0}.get(state), state


def momentum_12_1(s: pd.Series) -> float:
    """12-month return skipping the most recent month (standard momentum definition)."""
    s = s.dropna()
    if len(s) < 260:
        return np.nan
    return float(s.iloc[-22] / s.iloc[-253] - 1)


def _summarise(scores: dict[str, int | None], min_evidence: int) -> dict:
    vals = [v for v in scores.values() if v is not None]
    pos, neg = sum(v > 0 for v in vals), sum(v < 0 for v in vals)
    evidence = len(vals)
    return {"composite": int(sum(vals)) if vals else None, "evidence": evidence,
            "factors_total": len(scores), "positives": pos, "negatives": neg,
            "agreement": round(max(pos, neg) / evidence, 2) if evidence else None,
            "enough_evidence": evidence >= min_evidence}


def _confidence(summary: dict) -> str:
    if not summary["enough_evidence"]:
        return "none"
    if summary["evidence"] >= summary["factors_total"] - 1 and (summary["agreement"] or 0) >= 0.7:
        return "moderate"
    return "low"


# --------------------------------------------------------------------------- stocks
def stock_matrix(screen: pd.DataFrame, price_lookup, cfg: dict | None = None) -> pd.DataFrame:
    """screen: output of run_stock_screen. price_lookup(ticker) -> adj_close Series by date."""
    c = (cfg or load_config())["stocks"]
    rows = []
    for r in screen.to_dict("records"):
        s = price_lookup(r["ticker"])
        reasons, flags = [], []

        def pillar(*cols_pts):
            got = [(r.get(col), pts) for col, pts in cols_pts if not _isnan(r.get(col))]
            if not got:
                return None, np.nan
            frac = sum(v for v, _ in got) / sum(p for _, p in got)
            return _band(frac, c["pillar_pos"], c["pillar_neg"]), frac

        q, qf = pillar(("financial_strength_pts", 25), ("profitability_quality_pts", 20))
        v, vf = pillar(("valuation_pts", 20),)
        g, gf = pillar(("growth_durability_pts", 15),)
        t, tstate = trend_factor(s)
        mom = momentum_12_1(s)
        m = _band(mom, c["momentum_pos"], c["momentum_neg"])
        dd, vol = r.get("max_drawdown_5y"), r.get("volatility_3y")
        if _isnan(dd) or _isnan(vol):
            rk = None
        elif dd <= c["risk_bad_max_dd"] or vol >= c["risk_bad_max_vol"]:
            rk = -1
        elif dd >= c["risk_good_max_dd"] and vol <= c["risk_good_max_vol"]:
            rk = 1
        else:
            rk = 0
        scores = dict(zip(STOCK_FACTORS, [q, v, g, t, m, rk]))
        for name, val, detail in [
            ("quality", q, f"{qf:.0%} of quality points" if not _isnan(qf) else "no data"),
            ("valuation", v, f"{vf:.0%} of valuation points (vs peers)" if not _isnan(vf) else "no data"),
            ("growth", g, f"{gf:.0%} of growth points" if not _isnan(gf) else "no data"),
            ("trend", t, tstate),
            ("momentum", m, f"12-1m return {mom:+.1%}" if not _isnan(mom) else "under 1y of prices"),
            ("risk", rk, f"5y max DD {dd:.0%}, 3y vol {vol:.0%}" if rk is not None else "no data"),
        ]:
            reasons.append(f"{name}: {'+' if val == 1 else '-' if val == -1 else '0' if val == 0 else 'n/a'} ({detail})")
        if isinstance(r.get("notes"), str) and "Negative earnings" in r["notes"]:
            flags.append("negative earnings")
        if not _isnan(r.get("coverage")) and r["coverage"] < 0.6:
            flags.append("low fundamental coverage")

        summ = _summarise(scores, c["min_evidence"])
        if not summ["enough_evidence"]:
            verdict = "INSUFFICIENT DATA"
        elif summ["composite"] >= c["watch_at"] and not flags:
            verdict = "WATCH"
        elif summ["composite"] >= c["avoid_below"]:
            verdict = "RESEARCH FURTHER"
        else:
            verdict = "AVOID FOR NOW"
        rows.append({"asset_class": "stock", "id": r["ticker"], "name": r.get("name"),
                     "market": "CA" if r.get("exchange") in {"TSX", "TSXV"} else "US",
                     "sector": r.get("sector"), **scores, **summ, "verdict": verdict,
                     "confidence": _confidence(summ), "red_flags": "; ".join(flags),
                     "reasons": " | ".join(reasons), "screen_score": r.get("score"),
                     "is_synthetic": r.get("is_synthetic"), "validation": VALIDATION_NOTE})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- ETFs
def etf_matrix(etf_view: pd.DataFrame, price_lookup, cfg: dict | None = None) -> pd.DataFrame:
    c = (cfg or load_config())["etfs"]
    rows = []
    for r in etf_view.to_dict("records"):
        s = price_lookup(r["ticker"])
        t, tstate = trend_factor(s)
        mom = momentum_12_1(s)
        m = _band(mom, c["momentum_pos"], c["momentum_neg"])
        ra = (r["cagr_5y"] / r["volatility_5y"]) if not _isnan(r.get("cagr_5y")) and r.get("volatility_5y") else np.nan
        rr = _band(ra, c["sharpe_like_pos"], c["sharpe_like_neg"])
        d = _band(r.get("max_drawdown_5y"), c["dd_good"], c["dd_bad"])
        scores = dict(zip(ETF_FACTORS, [t, m, rr, d]))
        summ = _summarise(scores, c["min_evidence"])
        if not summ["enough_evidence"]:
            verdict = "INSUFFICIENT DATA"
        elif summ["composite"] >= c["watch_at"]:
            verdict = "WATCH"
        elif summ["composite"] >= c["avoid_below"]:
            verdict = "RESEARCH FURTHER"
        else:
            verdict = "AVOID FOR NOW"
        reasons = [f"trend: {tstate}",
                   f"momentum: 12-1m {mom:+.1%}" if not _isnan(mom) else "momentum: n/a",
                   f"return/vol (5y): {ra:.2f}" if not _isnan(ra) else "return/vol: n/a",
                   f"max drawdown (5y): {r.get('max_drawdown_5y'):.0%}" if not _isnan(r.get("max_drawdown_5y")) else "drawdown: n/a"]
        rows.append({"asset_class": "etf", "id": r["ticker"], "name": r.get("name"), "category": r.get("category"),
                     **scores, **summ, "verdict": verdict, "confidence": _confidence(summ), "red_flags": "",
                     "reasons": " | ".join(reasons), "is_synthetic": r.get("is_synthetic"),
                     "validation": VALIDATION_NOTE})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- forex
def _rate_differential(pair: str, macro: pd.DataFrame) -> float:
    """Base minus quote policy rate. Phase 1 only has USD (DFF) and CAD (V39079)."""
    policy = {"USD": "DFF", "CAD": "V39079"}
    base, quote = pair.split("/")
    if base not in policy or quote not in policy or macro is None or macro.empty:
        return np.nan
    last = macro.sort_values("date").groupby("series")["value"].last()
    if policy[base] not in last or policy[quote] not in last:
        return np.nan
    return float(last[policy[base]] - last[policy[quote]])


def fx_matrix(fx_long: pd.DataFrame, macro: pd.DataFrame | None = None, cfg: dict | None = None) -> pd.DataFrame:
    c = (cfg or load_config())["forex"]
    wide = build_pairs(fx_long)
    rows = []
    for pair in wide.columns:
        s = wide[pair].dropna()
        if len(s) < 2:
            continue  # no data for this pair yet
        t, tstate = trend_factor(s)
        chg3 = float(s.iloc[-1] / s.iloc[-64] - 1) if len(s) > 64 else np.nan
        hist = ind.macd(s)["hist"].iloc[-1] if len(s) > 35 else np.nan
        if _isnan(chg3) or _isnan(hist):
            m = None
        elif chg3 >= c["momentum_3m"] and hist > 0:
            m = 1
        elif chg3 <= -c["momentum_3m"] and hist < 0:
            m = -1
        else:
            m = 0
        rd = _rate_differential(pair, macro)
        r = _band(rd, c["rate_diff_pts"], -c["rate_diff_pts"])
        rsi = float(ind.rsi(s).iloc[-1]) if len(s) > 15 else np.nan
        st = None if _isnan(rsi) else (-1 if rsi >= c["rsi_overbought"] else 1 if rsi <= c["rsi_oversold"] else 0)
        scores = dict(zip(FX_FACTORS, [t, m, r, st]))
        summ = _summarise(scores, c["min_evidence"])
        if not summ["enough_evidence"]:
            verdict = "INSUFFICIENT EVIDENCE"
        elif summ["composite"] >= c["directional_at"]:
            verdict = "BULLISH"
        elif summ["composite"] <= -c["directional_at"]:
            verdict = "BEARISH"
        else:
            verdict = "NEUTRAL"
        lr = ind.log_returns(s)
        v3, v1 = lr.tail(63).std() * np.sqrt(252), lr.tail(252).std() * np.sqrt(252)
        flags = ["volatility spike (3m vol >> 1y vol)"] if v1 and v3 / v1 >= c["vol_spike_ratio"] else []
        conf = _confidence(summ)
        if flags and conf == "moderate":
            conf = "low"
        reasons = [f"trend: {tstate}",
                   f"momentum: 3m {chg3:+.1%}, MACD hist {'+' if hist > 0 else '-'}" if m is not None else "momentum: n/a",
                   f"rate differential: {rd:+.2f} pts" if r is not None else "rate differential: n/a (needs foreign policy rates, Phase 2)",
                   f"stretch: RSI {rsi:.0f}" if st is not None else "stretch: n/a"]
        # 1-sigma 1-month range as a plain uncertainty band (not a forecast)
        band = float(s.iloc[-1] * v3 * np.sqrt(21 / 252)) if not _isnan(v3) else np.nan
        rows.append({"asset_class": "forex", "id": pair, "name": pair, **scores, **summ, "verdict": verdict,
                     "confidence": conf, "red_flags": "; ".join(flags), "reasons": " | ".join(reasons),
                     "last": float(s.iloc[-1]), "one_month_1sigma_range": [float(s.iloc[-1] - band), float(s.iloc[-1] + band)]
                     if not _isnan(band) else None,
                     "is_synthetic": bool(fx_long["is_synthetic"].any()) if "is_synthetic" in fx_long else None,
                     "validation": VALIDATION_NOTE})
    return pd.DataFrame(rows)


def build_all(wh, cfg: dict | None = None) -> dict[str, pd.DataFrame]:
    """Convenience: full matrix from the warehouse."""
    from cra.screen import run_etf_view, run_stock_screen

    cache: dict[str, pd.Series] = {}

    def lookup(t):
        if t not in cache:
            px = wh.prices(t)
            cache[t] = px.set_index(pd.to_datetime(px["date"]))["adj_close"] if not px.empty else pd.Series(dtype=float)
        return cache[t]

    screens = pd.concat([run_stock_screen(wh, "CA"), run_stock_screen(wh, "US")], ignore_index=True)
    return {"stocks": stock_matrix(screens, lookup, cfg) if not screens.empty else pd.DataFrame(),
            "etfs": etf_matrix(run_etf_view(wh), lookup, cfg),
            "forex": fx_matrix(wh.fx(), wh.macro(), cfg)}
