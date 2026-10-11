"""Entry point for Streamlit Community Cloud (share.streamlit.io).

Set "Main file path" to streamlit_app.py.

Data source is chosen with the CRA_DATA_SOURCE secret:
  "synthetic" (default)  labelled demo data, loaded once.
  "live"                 real data from the providers whose keys are set
                         (Bank of Canada needs no key). Stored in a separate
                         database file and refreshed every CRA_REFRESH_HOURS (default 12).
"""

import os
import runpy
import sys
import threading
import time
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CRA_HOME", str(ROOT))

LIVE = os.environ.get("CRA_DATA_SOURCE", "synthetic").strip().lower() == "live"
if LIVE:
    # Keep live data apart from the demo database so the two never mix.
    os.environ.setdefault("CRA_DUCKDB_PATH", str(ROOT / "data" / "warehouse_live.duckdb"))

from cra.config import get_settings  # noqa: E402
from cra.store import Warehouse  # noqa: E402

REFRESH_HOURS = float(os.environ.get("CRA_REFRESH_HOURS", "12"))
_LOCK = threading.Lock()


def _needs_load(path: Path) -> bool:
    if not path.exists():
        return True
    if LIVE:
        age = time.time() - path.stat().st_mtime
        # Reload when data is stale, or when the stock list was changed in a newer deploy.
        universe_changed = (ROOT / "cra" / "universe_real.csv").stat().st_mtime > path.stat().st_mtime
        return age > REFRESH_HOURS * 3600 or universe_changed
    return False


settings = get_settings()
if _needs_load(settings.duckdb_path):
    with _LOCK:
        if _needs_load(settings.duckdb_path):  # another session may have just loaded it
            from cra.ingest import ingest_live, ingest_synthetic

            with st.spinner("Loading live market data. This can take a minute..." if LIVE else "Loading demo data..."):
                wh = Warehouse(settings.duckdb_path)
                try:
                    if LIVE:
                        status = ingest_live(wh, settings, ROOT / "cra" / "universe_real.csv", date(2015, 1, 1))
                        print("Live ingest status:", status, flush=True)
                    else:
                        ingest_synthetic(wh)
                finally:
                    wh.close()
                settings.duckdb_path.touch()

runpy.run_path(str(ROOT / "cra" / "dashboard.py"), run_name="__main__")
