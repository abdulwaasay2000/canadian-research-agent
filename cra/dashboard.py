"""Streamlit research dashboard (Phase 1).

Run: streamlit run cra/dashboard.py   (or python -m streamlit run streamlit_app.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make "import cra" work when run as a script

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from cra.indicators import sma
from cra.matrix import build_all
from cra.tracker import alerts, asset_trackers, fx_trackers, rate_trackers
from cra.config import get_settings
from cra.pairs import PAIRS
from cra.fx_research import pair_history, pair_summary
from cra.screen import run_etf_view, run_stock_screen
from cra.store import Warehouse

st.set_page_config(page_title="Canadian Research Agent", layout="wide")
settings = get_settings()

if not settings.duckdb_path.exists():
    st.error("No data warehouse found. Run `python -m cra.ingest --synthetic` (demo) "
             "or `--live` first.")
    st.stop()


@st.cache_data(ttl=300)
def load(_key: str):
    wh = Warehouse(settings.duckdb_path, read_only=True)
    try:
        return {
            "freshness": wh.freshness(), "universe": wh.universe(), "fx": wh.fx(), "macro": wh.macro(),
            "issues": wh.recent_issues(), "runs": wh.recent_runs(),
            "ca": run_stock_screen(wh, "CA"), "us": run_stock_screen(wh, "US"), "etf": run_etf_view(wh),
            "synthetic": wh.any_synthetic(),
            "t_assets": asset_trackers(wh), "t_fx": fx_trackers(wh.fx()), "t_rates": rate_trackers(wh.macro()),
            "matrix": build_all(wh),
        }
    finally:
        wh.close()


data = load(str(settings.duckdb_path.stat().st_mtime))

st.sidebar.title("Research Agent")
st.sidebar.caption(f"Mode: **{settings.mode}** · live trading: **disabled**")
page = st.sidebar.radio("Page", ["Decision Matrix", "Trackers", "Overview", "Canadian Screener", "US Screener", "ETFs", "Forex Research",
                                 "Investment Detail", "Data Quality"])

if data["synthetic"]:
    st.warning("**SYNTHETIC DEMO DATA IN USE.** Some or all numbers on this page are generated for "
               "demonstration and describe fictional companies. Do not use them for decisions.")
_fresh = data["freshness"]
_missing = [d for d in ("prices", "fx", "fundamentals") if _fresh.empty or d not in set(_fresh["dataset"])]
if _missing:
    st.info("No data loaded yet for: **" + ", ".join(_missing) + "**. "
            + ("Stock and ETF prices need a licensed price feed (EODHD_API_KEY). " if "prices" in _missing else "")
            + ("Exchange rates come from the Bank of Canada; check Data Quality for connection errors. " if "fx" in _missing else "")
            + ("US fundamentals need SEC_USER_AGENT. " if "fundamentals" in _missing else "")
            + "See the Data Quality page for details.")
st.caption("Research output only. Not investment, tax or legal advice. Labels are research triage, not trade signals.")


def pct(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out:
            out[c] = (out[c] * 100).round(1)
    return out


def screener_page(df: pd.DataFrame, title: str, show_cad: bool) -> None:
    st.header(title)
    if df.empty:
        st.info("No stocks for this market in the warehouse.")
        return
    labels = st.multiselect("Label", sorted(df["label"].unique()), default=sorted(df["label"].unique()))
    sectors = st.multiselect("Sector", sorted(df["sector"].unique()), default=sorted(df["sector"].unique()))
    view = df[df["label"].isin(labels) & df["sector"].isin(sectors)]
    cols = ["ticker", "name", "exchange", "sector", "price", "currency"] + (["price_cad"] if show_cad else []) + [
        "price_date", "score", "max_possible", "coverage", "label", "financial_strength_pts",
        "profitability_quality_pts", "valuation_pts", "growth_durability_pts", "risk_governance_pts",
        "pe", "ev_ebitda", "p_b", "roe", "roic", "revenue_cagr_5y", "debt_to_equity", "latest_fy",
        "statements_available_since", "price_source", "peer_fallback"]
    show = pct(view[[c for c in cols if c in view]], ["roe", "roic", "revenue_cagr_5y"])
    st.dataframe(show, hide_index=True, width="stretch")
    st.caption("Score = peer-percentile points per pillar x data coverage. Missing data earns 0 points; "
               "not-applicable metrics (e.g. EV/EBITDA for banks) are excluded. Competitive position "
               "(10 pts) is not auto-scored yet, so max_possible is 90. % columns shown x100. "
               "peer_fallback = sector had <3 peers so the whole market was used.")



FACTOR_SYMBOL = {1: "▲ +", 0: "● 0", -1: "▼ −"}
VERDICT_COLOR = {"WATCH": "#d6f5dd", "BULLISH": "#d6f5dd", "RESEARCH FURTHER": "#fff4cc", "NEUTRAL": "#eeeeee",
                 "AVOID FOR NOW": "#fbd9d9", "BEARISH": "#fbd9d9", "INSUFFICIENT DATA": "#e4e4f7",
                 "INSUFFICIENT EVIDENCE": "#e4e4f7"}


def matrix_table(df: pd.DataFrame, factors: list[str]) -> None:
    if df.empty:
        st.info("Nothing to show.")
        return
    view = df[["id", "name"] + factors + ["composite", "evidence", "verdict", "confidence", "red_flags"]].copy()
    for f in factors:
        view[f] = view[f].map(lambda v: "n/a" if pd.isna(v) else FACTOR_SYMBOL[int(v)])
    styled = view.style.apply(lambda col: [f"background-color: {VERDICT_COLOR.get(v, '')}" for v in col],
                              subset=["verdict"]).apply(
        lambda col: [("color: #1a7f37" if str(v).startswith("▲") else "color: #b42318" if str(v).startswith("▼")
                      else "color: #888") for v in col], subset=factors)
    st.dataframe(styled, hide_index=True, width="stretch")
    with st.expander("Why? Factor-by-factor reasons"):
        for r in df.itertuples():
            st.markdown(f"**{r.id}** — {r.verdict} · " + r.reasons.replace(" | ", " · "))


def tracker_table(df: pd.DataFrame, id_col: str, extra: list[str]) -> None:
    cols = [id_col] + extra + ["last", "as_of", "chg_1d", "chg_1w", "chg_1m", "chg_3m", "chg_ytd", "chg_1y",
                               "pct_from_52w_high", "spark"]
    view = df[[c for c in cols if c in df]].copy()
    cfg = {c: st.column_config.NumberColumn(c, format="percent") for c in
           ["chg_1d", "chg_1w", "chg_1m", "chg_3m", "chg_ytd", "chg_1y", "pct_from_52w_high"]}
    cfg["spark"] = st.column_config.LineChartColumn("90d", width="small")
    st.dataframe(view, hide_index=True, width="stretch", column_config=cfg)


if page == "Decision Matrix":
    st.header("Decision Matrix")
    st.caption("Each factor: ▲ supportive, ● neutral, ▼ unsupportive, n/a no data (never counted as neutral). "
               "Verdicts are research triage labels. The rules in config/decision_matrix.json are not yet backtested.")
    m = data["matrix"]
    tab_s, tab_e, tab_f = st.tabs(["Stocks", "ETFs", "Forex"])
    with tab_s:
        mk = st.radio("Market", ["All", "CA", "US"], horizontal=True)
        sdf = m["stocks"] if mk == "All" else m["stocks"][m["stocks"]["market"] == mk]
        c1, c2, c3, c4 = st.columns(4)
        for col, lab in zip((c1, c2, c3, c4), ["WATCH", "RESEARCH FURTHER", "AVOID FOR NOW", "INSUFFICIENT DATA"]):
            col.metric(lab, int((sdf["verdict"] == lab).sum()) if not sdf.empty else 0)
        matrix_table(sdf.sort_values("composite", ascending=False) if not sdf.empty else sdf,
                     ["quality", "valuation", "growth", "trend", "momentum", "risk"])
    with tab_e:
        matrix_table(m["etfs"], ["trend", "momentum", "risk_adj_return", "drawdown"])
    with tab_f:
        st.caption("BULLISH/BEARISH refer to the first currency vs the second (USD/CAD bullish = USD stronger). "
                   "Range = ±1 sigma one-month band from recent volatility, an uncertainty guide, not a forecast.")
        fdf = m["forex"].copy()
        if not fdf.empty:
            fdf["name"] = fdf["one_month_1sigma_range"].map(lambda r: f"{r[0]:.4f} – {r[1]:.4f}" if r else "n/a")
        matrix_table(fdf, ["trend", "momentum", "rate_differential", "stretch"])

elif page == "Trackers":
    st.header("Trackers")
    ta, tf, tr = data["t_assets"], data["t_fx"], data["t_rates"]
    al = alerts(ta, tf)
    st.subheader(f"Alerts ({len(al)})")
    if al.empty:
        st.write("No alerts.")
    else:
        st.dataframe(al, hide_index=True, width="stretch")
    t1, t2, t3, t4, t5 = st.tabs(["Forex", "Canadian stocks", "US stocks", "ETFs", "Rates"])
    with t1:
        tracker_table(tf, "pair", [])
    with t2:
        tracker_table(ta[(ta["country"] == "CA") & (ta["asset_type"] == "stock")], "ticker", ["name", "sector"])
    with t3:
        tracker_table(ta[(ta["country"] == "US") & (ta["asset_type"] == "stock")], "ticker", ["name", "sector"])
    with t4:
        tracker_table(ta[ta["asset_type"] == "etf"], "ticker", ["name", "currency"])
    with t5:
        st.dataframe(tr, hide_index=True, width="stretch")

elif page == "Overview":
    st.header("Overview")
    f = data["freshness"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Last price observation", str(f.loc[f["dataset"] == "prices", "last_observation"].max()))
    c2.metric("Last FX observation", str(f.loc[f["dataset"] == "fx", "last_observation"].max()))
    c3.metric("Last data refresh (UTC)", str(pd.to_datetime(f["last_retrieved"]).max())[:19])
    st.subheader("Rates")
    m = data["macro"]
    if not m.empty:
        last = m.sort_values("date").groupby("series").tail(1)[["series", "value", "date", "source", "is_synthetic"]]
        st.dataframe(last, hide_index=True, width="stretch")
    st.subheader("Exchange rates")
    st.dataframe(pct(pair_summary(data["fx"]), ["chg_1w", "chg_1m", "chg_1y", "vol_3m_ann", "vol_1y_ann"]),
                 hide_index=True, width="stretch")
    st.subheader("Research watchlist (label = WATCH)")
    wl = pd.concat([data["ca"], data["us"]])
    wl = wl[wl["label"] == "WATCH"][["ticker", "name", "sector", "score", "coverage", "price", "currency", "price_date"]]
    if wl.empty:
        st.write("Nothing currently meets the WATCH threshold. That is a valid result.")
    else:
        st.dataframe(wl, hide_index=True, width="stretch")

elif page == "Canadian Screener":
    screener_page(data["ca"], "Canadian Stock Screener (TSX / TSXV)", show_cad=False)

elif page == "US Screener":
    screener_page(data["us"], "US Stock Screener (NYSE / NASDAQ)", show_cad=True)
    st.caption("CAD conversion uses the Bank of Canada USD/CAD rate on or before the price date.")

elif page == "ETFs":
    st.header("ETFs")
    st.dataframe(pct(data["etf"], ["cagr_5y", "volatility_5y", "max_drawdown_5y"]), hide_index=True,
                 width="stretch")
    st.caption("Phase 1 shows return/risk only. Fees (MER), tracking error and holdings come in Phase 2.")

elif page == "Forex Research":
    st.header("Forex Research")
    st.info("Pairs are built from Bank of Canada daily indicative rates (CAD crosses direct, others computed). "
            "They are not tradable quotes and include no bid/ask spread. Trend state is descriptive, not a forecast.")
    pair = st.selectbox("Pair", list(PAIRS))
    if data["fx"].empty:
        st.warning("No exchange-rate data loaded yet.")
        st.stop()
    h = pair_history(data["fx"], pair).tail(750)
    fig = go.Figure()
    for c, w in [("close", 2), ("sma_20", 1), ("sma_50", 1), ("sma_200", 1)]:
        fig.add_scatter(x=h.index, y=h[c], name=c, line={"width": w})
    fig.add_scatter(x=h.index, y=h["upper"], name="Bollinger upper", line={"dash": "dot", "width": 1})
    fig.add_scatter(x=h.index, y=h["lower"], name="Bollinger lower", line={"dash": "dot", "width": 1})
    fig.update_layout(height=420, margin={"t": 20})
    st.plotly_chart(fig, width="stretch")
    c1, c2 = st.columns(2)
    c1.line_chart(h["rsi_14"], height=200)
    c2.line_chart(h[["macd", "signal"]], height=200)
    st.dataframe(pair_summary(data["fx"]), hide_index=True, width="stretch")

elif page == "Investment Detail":
    st.header("Investment Detail")
    uni = data["universe"]
    t = st.selectbox("Ticker", uni["ticker"])
    row = uni[uni["ticker"] == t].iloc[0]
    st.subheader(f"{row['name']} ({t}, {row['exchange']}) · {row['sector']}")
    wh = Warehouse(settings.duckdb_path, read_only=True)
    try:
        px, fund = wh.prices(t), wh.fundamentals(t)
    finally:
        wh.close()
    if not px.empty:
        s = px.set_index(pd.to_datetime(px["date"]))["adj_close"]
        fig = go.Figure([go.Scatter(x=s.index, y=s, name="adj close"),
                         go.Scatter(x=s.index, y=sma(s, 200), name="200d SMA")])
        fig.update_layout(height=360, margin={"t": 20})
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Source: {px['source'].iloc[-1]} · synthetic: {bool(px['is_synthetic'].iloc[-1])} · "
                   f"last: {px['date'].iloc[-1]}")
    if not fund.empty:
        st.subheader("Annual statements (as first reported)")
        st.dataframe(fund.pivot_table(index="metric", columns="fiscal_year", values="value").round(1),
                     width="stretch")
    rec = pd.concat([data["ca"], data["us"]])
    rec = rec[rec["ticker"] == t]
    if not rec.empty:
        st.subheader("Metric snapshot")
        st.dataframe(rec.T.rename(columns={rec.index[0]: "value"}).astype(str), width="stretch")

elif page == "Data Quality":
    st.header("Data Quality & Sources")
    st.subheader("Freshness by source")
    st.dataframe(data["freshness"], hide_index=True, width="stretch")
    st.subheader("Validation issues")
    st.dataframe(data["issues"], hide_index=True, width="stretch")
    st.subheader("Ingestion runs")
    st.dataframe(data["runs"], hide_index=True, width="stretch")
