"""FRED (St. Louis Fed) client for US macro series. Requires a free API key (FRED_API_KEY).

Endpoint: https://api.stlouisfed.org/fred/series/observations
          ?series_id=DGS10&api_key=KEY&file_type=json&observation_start=YYYY-MM-DD
Missing values are returned as "." and are dropped (never filled).

Caveat: FRED returns the *latest revised* values. For point-in-time macro research use
ALFRED vintages (realtime_start/realtime_end) - planned for Phase 3 leakage tests.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from cra.http import DataSourceError, HttpClient
from cra.models import Dataset, Provenance

URL = "https://api.stlouisfed.org/fred/series/observations"

US_MACRO_SERIES = {
    "DFF": "Effective federal funds rate (%)",
    "DGS2": "US Treasury 2y constant maturity (%)",
    "DGS10": "US Treasury 10y constant maturity (%)",
    "CPIAUCSL": "US CPI-U, all items, SA (index)",
    "UNRATE": "US unemployment rate (%)",
}


def parse_fred(payload: dict, series_id: str) -> pd.DataFrame:
    if "observations" not in payload:
        raise DataSourceError(f"FRED payload for {series_id} missing 'observations'")
    rows = [
        {"date": pd.Timestamp(o["date"]), "series": series_id, "value": float(o["value"])}
        for o in payload["observations"] if o.get("value") not in (".", "", None)
    ]
    return pd.DataFrame(rows, columns=["date", "series", "value"])


class FredClient:
    def __init__(self, http: HttpClient, api_key: str | None) -> None:
        if not api_key:
            raise DataSourceError("FRED_API_KEY not set; FRED provider disabled.")
        self.http, self.api_key = http, api_key

    def series(self, series_ids: list[str] | None = None, start: date | None = None) -> Dataset:
        frames = []
        for sid in series_ids or list(US_MACRO_SERIES):
            params = {"series_id": sid, "api_key": self.api_key, "file_type": "json"}
            if start:
                params["observation_start"] = start.isoformat()
            frames.append(parse_fred(self.http.get_json(URL, params=params).payload, sid))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series", "value"])
        return Dataset("macro", df, Provenance(source="fred", url=URL,
                                               notes="Latest-revised values (not point-in-time)."))
