"""Entry point for Streamlit Community Cloud (share.streamlit.io).

Set "Main file path" to streamlit_app.py. On first run it loads demo data
(or live data when the CRA_DATA_SOURCE secret is "live"), then shows the dashboard.
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CRA_HOME", str(ROOT))

from cra.config import get_settings  # noqa: E402
from cra.store import Warehouse  # noqa: E402

settings = get_settings()
if not settings.duckdb_path.exists():
    from cra.ingest import ingest_live, ingest_synthetic

    wh = Warehouse(settings.duckdb_path)
    try:
        if os.environ.get("CRA_DATA_SOURCE", "synthetic") == "live":
            from datetime import date
            ingest_live(wh, settings, ROOT / "cra" / "universe_real.csv", date(2015, 1, 1))
        else:
            ingest_synthetic(wh)
    finally:
        wh.close()

runpy.run_path(str(ROOT / "cra" / "dashboard.py"), run_name="__main__")
