"""DuckDB analytical warehouse. Every row keeps its source, synthetic flag and retrieval time."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import duckdb
import pandas as pd

from cra.models import Dataset, utcnow

log = logging.getLogger(__name__)

KEYS = {
    "prices": ["ticker", "date"],
    "fx": ["series", "date"],
    "macro": ["series", "date"],
    "fundamentals": ["ticker", "metric", "period_end", "available_date"],
}

DDL = """
CREATE TABLE IF NOT EXISTS prices (
  date DATE, ticker VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, adj_close DOUBLE,
  volume BIGINT, currency VARCHAR, source VARCHAR, is_synthetic BOOLEAN, retrieved_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS fx (
  date DATE, series VARCHAR, value DOUBLE, source VARCHAR, is_synthetic BOOLEAN, retrieved_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS macro (
  date DATE, series VARCHAR, value DOUBLE, source VARCHAR, is_synthetic BOOLEAN, retrieved_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS fundamentals (
  ticker VARCHAR, metric VARCHAR, period_end DATE, fiscal_year INTEGER, value DOUBLE, unit VARCHAR,
  available_date DATE, form VARCHAR, source VARCHAR, is_synthetic BOOLEAN, retrieved_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS universe (
  ticker VARCHAR, name VARCHAR, exchange VARCHAR, country VARCHAR, sector VARCHAR, industry VARCHAR,
  asset_type VARCHAR, currency VARCHAR, is_synthetic BOOLEAN);
CREATE TABLE IF NOT EXISTS ingestion_log (
  run_at TIMESTAMPTZ, kind VARCHAR, source VARCHAR, rows BIGINT, status VARCHAR, detail VARCHAR,
  provenance JSON);
CREATE TABLE IF NOT EXISTS quality_issues (
  run_at TIMESTAMPTZ, kind VARCHAR, severity VARCHAR, check_name VARCHAR, subject VARCHAR, detail VARCHAR);
"""


# One DuckDB connection per database file per process. DuckDB refuses to open the same file
# twice in one process with different settings (e.g. read-only + read-write), which happens in
# Streamlit/FastAPI where many sessions share a process. Each Warehouse gets its own cursor.
_SHARED: dict[str, duckdb.DuckDBPyConnection] = {}
_LOCK = threading.Lock()


def _shared_connection(path: str, read_only: bool) -> duckdb.DuckDBPyConnection:
    key = str(Path(path).resolve())
    with _LOCK:
        con = _SHARED.get(key)
        if con is None:
            con = duckdb.connect(key, read_only=read_only)
            _SHARED[key] = con
        return con


def reset_connections() -> None:
    """Close all shared connections (tests, or before another process needs to write)."""
    with _LOCK:
        for con in _SHARED.values():
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        _SHARED.clear()


class Warehouse:
    def __init__(self, path: str | Path = ":memory:", read_only: bool = False) -> None:
        if str(path) == ":memory:":
            self.con = duckdb.connect(":memory:")
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.con = _shared_connection(str(path), read_only).cursor()
        try:
            self.con.execute(DDL)
        except duckdb.Error:
            pass  # read-only database that already has its tables

    def close(self) -> None:
        self.con.close()  # closes this cursor only; the shared connection stays open

    # ---- writes ---------------------------------------------------------------
    def upsert(self, ds: Dataset) -> int:
        df = ds.frame.copy()
        if df.empty:
            self.log_run(ds.kind, ds.provenance.source, 0, "empty", "No rows returned", ds)
            return 0
        df["source"] = ds.provenance.source
        df["is_synthetic"] = ds.provenance.is_synthetic
        df["retrieved_at"] = pd.Timestamp(ds.provenance.retrieved_at)
        keys = KEYS[ds.kind]
        self.con.register("incoming", df)
        on = " AND ".join(f"t.{k} = incoming.{k}" for k in keys)
        self.con.execute(f"DELETE FROM {ds.kind} t USING incoming WHERE {on}")
        cols = ", ".join(df.columns)
        self.con.execute(f"INSERT INTO {ds.kind} ({cols}) SELECT {cols} FROM incoming")
        self.con.unregister("incoming")
        self.log_run(ds.kind, ds.provenance.source, len(df), "ok", ds.provenance.notes, ds)
        return len(df)

    def replace_universe(self, universe: pd.DataFrame) -> None:
        self.con.execute("DELETE FROM universe")
        self.con.register("u", universe)
        self.con.execute("INSERT INTO universe SELECT ticker, name, exchange, country, sector, industry, "
                         "asset_type, currency, is_synthetic FROM u")
        self.con.unregister("u")

    def log_run(self, kind: str, source: str, rows: int, status: str, detail: str = "",
                ds: Dataset | None = None) -> None:
        prov = json.dumps(ds.provenance.as_dict()) if ds else None
        self.con.execute("INSERT INTO ingestion_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [utcnow(), kind, source, rows, status, detail, prov])

    def log_issues(self, kind: str, issues: list) -> None:
        now = utcnow()
        for i in issues:
            self.con.execute("INSERT INTO quality_issues VALUES (?, ?, ?, ?, ?, ?)",
                             [now, kind, i.severity, i.check, i.subject, i.detail])

    # ---- reads ----------------------------------------------------------------
    def query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        return self.con.execute(sql, params or []).fetchdf()

    def prices(self, ticker: str, start: str | None = None) -> pd.DataFrame:
        sql = "SELECT * FROM prices WHERE ticker = ?" + (" AND date >= ?" if start else "") + " ORDER BY date"
        return self.query(sql, [ticker] + ([start] if start else []))

    def fx(self, series: list[str] | None = None) -> pd.DataFrame:
        if series:
            ph = ",".join("?" * len(series))
            return self.query(f"SELECT * FROM fx WHERE series IN ({ph}) ORDER BY series, date", series)
        return self.query("SELECT * FROM fx ORDER BY series, date")

    def macro(self) -> pd.DataFrame:
        return self.query("SELECT * FROM macro ORDER BY series, date")

    def fundamentals(self, ticker: str | None = None, as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
        """Point-in-time fundamentals: only rows whose available_date <= as_of."""
        sql, params = "SELECT * FROM fundamentals WHERE 1=1", []
        if ticker:
            sql += " AND ticker = ?"
            params.append(ticker)
        if as_of is not None:
            sql += " AND available_date <= ?"
            params.append(pd.Timestamp(as_of).date())
        return self.query(sql + " ORDER BY ticker, metric, period_end", params)

    def universe(self) -> pd.DataFrame:
        return self.query("SELECT * FROM universe ORDER BY country, ticker")

    def freshness(self) -> pd.DataFrame:
        return self.query("""
            SELECT 'prices' AS dataset, source, bool_or(is_synthetic) AS is_synthetic,
                   max(date) AS last_observation, max(retrieved_at) AS last_retrieved, count(*) AS rows FROM prices GROUP BY source
            UNION ALL SELECT 'fx', source, bool_or(is_synthetic), max(date), max(retrieved_at), count(*) FROM fx GROUP BY source
            UNION ALL SELECT 'macro', source, bool_or(is_synthetic), max(date), max(retrieved_at), count(*) FROM macro GROUP BY source
            UNION ALL SELECT 'fundamentals', source, bool_or(is_synthetic), max(period_end), max(retrieved_at), count(*) FROM fundamentals GROUP BY source
            ORDER BY dataset""")

    def any_synthetic(self) -> bool:
        df = self.freshness()
        return bool(df["is_synthetic"].any()) if not df.empty else False

    def recent_issues(self, limit: int = 200) -> pd.DataFrame:
        return self.query("SELECT * FROM quality_issues ORDER BY run_at DESC LIMIT ?", [limit])

    def recent_runs(self, limit: int = 50) -> pd.DataFrame:
        return self.query("SELECT run_at, kind, source, rows, status, detail FROM ingestion_log "
                          "ORDER BY run_at DESC LIMIT ?", [limit])
