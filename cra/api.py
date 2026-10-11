"""FastAPI backend (read-only research API).

Run: uvicorn cra.api:app --reload
There are no order-placement endpoints by design.
"""

from __future__ import annotations

import math
from contextlib import contextmanager

import pandas as pd
from fastapi import FastAPI, HTTPException

from cra import __version__
from cra.config import get_settings
from cra.pairs import PAIRS
from cra.fx_research import pair_history, pair_summary
from cra.screen import run_etf_view, run_stock_screen
from cra.store import Warehouse

app = FastAPI(title="Canadian Research Agent API", version=__version__,
              description="Research and paper-trading data API. Not financial advice. No live trading.")


@contextmanager
def warehouse():
    settings = get_settings()
    if not settings.duckdb_path.exists():
        raise HTTPException(503, "No warehouse yet. Run: python -m cra.ingest --synthetic")
    wh = Warehouse(settings.duckdb_path, read_only=True)
    try:
        yield wh
    finally:
        wh.close()


def records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe records (NaN -> None, timestamps -> ISO strings)."""
    out = []
    for rec in df.to_dict(orient="records"):
        clean = {}
        for k, v in rec.items():
            if isinstance(v, float) and math.isnan(v):
                v = None
            elif isinstance(v, (pd.Timestamp,)) or hasattr(v, "isoformat"):
                v = v.isoformat()
            elif v is pd.NaT:
                v = None
            clean[k] = v
        out.append(clean)
    return out


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "version": __version__, "mode": s.mode, "live_trading": False}


@app.get("/status")
def status() -> dict:
    with warehouse() as wh:
        return {"synthetic_data_in_use": wh.any_synthetic(), "freshness": records(wh.freshness()),
                "recent_runs": records(wh.recent_runs(20)), "quality_issues": records(wh.recent_issues(50))}


@app.get("/universe")
def universe() -> list[dict]:
    with warehouse() as wh:
        return records(wh.universe())


@app.get("/screener/{market}")
def screener(market: str, as_of: str | None = None) -> dict:
    market = market.upper()
    if market not in {"CA", "US"}:
        raise HTTPException(404, "market must be CA or US")
    with warehouse() as wh:
        df = run_stock_screen(wh, market, as_of)
        return {"market": market, "disclaimer": "Research labels only; not recommendations.",
                "results": records(df)}


@app.get("/etfs")
def etfs() -> list[dict]:
    with warehouse() as wh:
        return records(run_etf_view(wh))


@app.get("/prices/{ticker}")
def prices(ticker: str, start: str | None = None) -> list[dict]:
    with warehouse() as wh:
        df = wh.prices(ticker.upper(), start)
        if df.empty:
            raise HTTPException(404, f"No prices for {ticker}")
        return records(df)


@app.get("/forex")
def forex() -> list[dict]:
    with warehouse() as wh:
        return records(pair_summary(wh.fx()))


@app.get("/forex/{base}/{quote}")
def forex_pair(base: str, quote: str, tail: int = 500) -> dict:
    pair = f"{base.upper()}/{quote.upper()}"
    if pair not in PAIRS:
        raise HTTPException(404, f"Unsupported pair {pair}. Supported: {list(PAIRS)}")
    with warehouse() as wh:
        hist = pair_history(wh.fx(), pair).tail(tail).reset_index().rename(columns={"index": "date", "date": "date"})
        return {"pair": pair, "note": "Derived from Bank of Canada daily indicative rates; no bid/ask.",
                "history": records(hist)}


@app.get("/macro")
def macro() -> list[dict]:
    with warehouse() as wh:
        return records(wh.query("SELECT series, arg_max(value, date) AS last_value, max(date) AS as_of, "
                                "any_value(source) AS source, bool_or(is_synthetic) AS is_synthetic "
                                "FROM macro GROUP BY series ORDER BY series"))


@app.get("/trackers")
def trackers() -> dict:
    from cra.export_snapshot import records as snap_records
    from cra.tracker import alerts, asset_trackers, fx_trackers, rate_trackers
    with warehouse() as wh:
        a, f = asset_trackers(wh), fx_trackers(wh.fx())
        return {"assets": snap_records(a), "fx": snap_records(f), "rates": snap_records(rate_trackers(wh.macro())),
                "alerts": snap_records(alerts(a, f))}


@app.get("/decision-matrix")
def decision_matrix() -> dict:
    from cra.matrix import VALIDATION_NOTE, build_all
    from cra.export_snapshot import records as snap_records
    with warehouse() as wh:
        m = build_all(wh)
        return {"note": VALIDATION_NOTE, **{k: snap_records(v) for k, v in m.items()}}
