"""EODHD end-of-day price provider (licensed, paid key: EODHD_API_KEY).

Endpoint: https://eodhd.com/api/eod/{SYMBOL}?api_token=KEY&fmt=json&from=YYYY-MM-DD&to=YYYY-MM-DD
Symbol suffixes: TSX ".TO", TSX Venture ".V", US ".US" (e.g. RY.TO, SHOP.TO, AAPL.US).
Fields: date, open, high, low, close, adjusted_close, volume.

STATUS: implemented against the documented format but NOT yet exercised against the live API
from this project (network was unavailable when it was written). Run
`python -m cra.ingest --check-providers` after adding a key, and confirm TSX/TSXV
coverage and licensing terms (personal vs. display/redistribution) before relying on it.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from cra.http import DataSourceError, HttpClient
from cra.models import PRICE_COLUMNS, Dataset, Provenance

URL = "https://eodhd.com/api/eod/{symbol}"

EXCHANGE_SUFFIX = {"TSX": ".TO", "TSXV": ".V", "NYSE": ".US", "NASDAQ": ".US", "NYSEARCA": ".US"}
EXCHANGE_CCY = {"TSX": "CAD", "TSXV": "CAD", "NYSE": "USD", "NASDAQ": "USD", "NYSEARCA": "USD"}


def to_symbol(ticker: str, exchange: str) -> str:
    try:
        return f"{ticker.upper()}{EXCHANGE_SUFFIX[exchange.upper()]}"
    except KeyError as exc:
        raise DataSourceError(f"Unsupported exchange {exchange}") from exc


def parse_eod(payload: list, ticker: str, currency: str) -> pd.DataFrame:
    if not isinstance(payload, list):
        raise DataSourceError(f"Unexpected EODHD payload for {ticker}: {str(payload)[:200]}")
    df = pd.DataFrame(payload)
    if df.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    df = df.rename(columns={"adjusted_close": "adj_close"})
    df["date"] = pd.to_datetime(df["date"])
    df["ticker"], df["currency"] = ticker.upper(), currency
    return df[PRICE_COLUMNS]


class EodhdClient:
    def __init__(self, http: HttpClient, api_key: str | None) -> None:
        if not api_key:
            raise DataSourceError("EODHD_API_KEY not set; EODHD provider disabled.")
        self.http, self.api_key = http, api_key

    def prices(self, ticker: str, exchange: str, start: date | None = None, end: date | None = None) -> Dataset:
        symbol = to_symbol(ticker, exchange)
        params = {"api_token": self.api_key, "fmt": "json"}
        if start:
            params["from"] = start.isoformat()
        if end:
            params["to"] = end.isoformat()
        payload = self.http.get_json(URL.format(symbol=symbol), params=params).payload
        df = parse_eod(payload, ticker, EXCHANGE_CCY[exchange.upper()])
        return Dataset("prices", df, Provenance(source="eodhd", url=URL.format(symbol=symbol),
                                                notes="End-of-day; adj_close per EODHD methodology."))
