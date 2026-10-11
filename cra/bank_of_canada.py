"""Bank of Canada Valet API client (no API key required).

Docs: https://www.bankofcanada.ca/valet/docs
Observations URL: https://www.bankofcanada.ca/valet/observations/{SERIES[,SERIES]}/json
Response shape:
  {"seriesDetail": {...}, "observations": [{"d": "YYYY-MM-DD", "FXUSDCAD": {"v": "1.3316"}}, ...]}

IMPORTANT data caveats (surfaced in the dashboard):
* BoC FX rates are a single daily *indicative* rate per currency, published once per business
  day (~16:30 ET). They are NOT tradable quotes and contain NO bid/ask spread.
* The current FX series begin 2017-01-03. Older history used different (noon/closing) series.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from cra.http import DataSourceError, HttpClient
from cra.models import Dataset, Provenance

log = logging.getLogger(__name__)

BASE_URL = "https://www.bankofcanada.ca/valet"

# CAD per 1 unit of foreign currency.
FX_SERIES = {
    "USD": "FXUSDCAD",
    "EUR": "FXEURCAD",
    "GBP": "FXGBPCAD",
    "JPY": "FXJPYCAD",
    "AUD": "FXAUDCAD",
}

# Macro series. Codes should be re-verified against /valet/lists/series/json on first run;
# `verify_series()` does this and logs anything that no longer exists.
MACRO_SERIES = {
    "V39079": "Target for the overnight rate (%)",
    "BD.CDN.2YR.DQ.YLD": "GoC benchmark bond yield, 2 year (%)",
    "BD.CDN.10YR.DQ.YLD": "GoC benchmark bond yield, 10 year (%)",
}


def parse_observations(payload: dict, series: list[str]) -> pd.DataFrame:
    """Turn a Valet observations payload into long format [date, series, value]."""
    if "observations" not in payload:
        raise DataSourceError("Bank of Canada payload has no 'observations' key")
    rows = []
    for obs in payload["observations"]:
        d = obs.get("d")
        for s in series:
            cell = obs.get(s)
            if not cell or cell.get("v") in (None, ""):
                continue  # missing observation: leave it missing, never fill silently
            try:
                rows.append({"date": pd.Timestamp(d), "series": s, "value": float(cell["v"])})
            except (TypeError, ValueError):
                log.warning("Unparseable BoC value %r for %s on %s", cell.get("v"), s, d)
    df = pd.DataFrame(rows, columns=["date", "series", "value"])
    return df.sort_values(["series", "date"]).reset_index(drop=True)


class BankOfCanadaClient:
    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def observations(self, series: list[str], start: date | None = None, end: date | None = None,
                     kind: str = "fx") -> Dataset:
        url = f"{BASE_URL}/observations/{','.join(series)}/json"
        params = {}
        if start:
            params["start_date"] = start.isoformat()
        if end:
            params["end_date"] = end.isoformat()
        resp = self.http.get_json(url, params=params)
        df = parse_observations(resp.payload, series)
        prov = Provenance(
            source="bank_of_canada_valet",
            url=url,
            notes=("Daily indicative rate, not a tradable quote; no bid/ask."
                   if kind == "fx" else "Official BoC statistic.")
                  + (" Served from cache." if resp.from_cache else ""),
        )
        return Dataset(kind=kind, frame=df, provenance=prov)

    def fx(self, start: date | None = None, end: date | None = None) -> Dataset:
        return self.observations(list(FX_SERIES.values()), start, end, kind="fx")

    def macro(self, start: date | None = None, end: date | None = None) -> Dataset:
        return self.observations(list(MACRO_SERIES), start, end, kind="macro")

    def verify_series(self) -> dict[str, bool]:
        """Check our configured series codes still exist in the Valet catalogue."""
        resp = self.http.get_json(f"{BASE_URL}/lists/series/json")
        known = set((resp.payload.get("series") or {}).keys())
        result = {s: s in known for s in [*FX_SERIES.values(), *MACRO_SERIES]}
        for s, ok in result.items():
            if not ok:
                log.error("BoC series %s not found in Valet catalogue - update config", s)
        return result
