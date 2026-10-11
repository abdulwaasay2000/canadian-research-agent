"""SEC EDGAR XBRL client (free, no key; a descriptive User-Agent with contact email is required
by SEC fair-access rules, max 10 requests/second).

Endpoints:
  https://www.sec.gov/files/company_tickers.json               ticker -> CIK map
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json all XBRL facts for a filer

Point-in-time rule: each annual value is stamped with the `filed` date of the FIRST filing that
reported it. Later restatements are kept as separate rows only if you request them, so a
backtest can never use a number before it was public.
"""

from __future__ import annotations

import logging

import pandas as pd

from cra.http import DataSourceError, HttpClient
from cra.models import Dataset, Provenance

log = logging.getLogger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Our metric name -> ordered list of US-GAAP concepts to try (first one with data wins).
CONCEPTS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "shareholders_equity": ["StockholdersEquity"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "depreciation_amortization": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "40-F"}


def parse_company_facts(payload: dict, ticker: str, first_filed_only: bool = True) -> pd.DataFrame:
    facts = (payload.get("facts") or {}).get("us-gaap") or {}
    rows = []
    for metric, concepts in CONCEPTS.items():
        for concept in concepts:
            node = facts.get(concept)
            if not node:
                continue
            for unit, items in (node.get("units") or {}).items():
                for it in items:
                    if it.get("form") not in ANNUAL_FORMS or it.get("fp") != "FY":
                        continue
                    # Duration facts must span roughly a year (filters quarterly values tagged FY).
                    if it.get("start"):
                        days = (pd.Timestamp(it["end"]) - pd.Timestamp(it["start"])).days
                        if not 350 <= days <= 380:
                            continue
                    rows.append({
                        "ticker": ticker, "metric": metric,
                        "period_end": pd.Timestamp(it["end"]),
                        "fiscal_year": int(pd.Timestamp(it["end"]).year),
                        "value": float(it["val"]), "unit": unit,
                        "available_date": pd.Timestamp(it["filed"]), "form": it["form"],
                    })
            if any(r["metric"] == metric for r in rows):
                break  # first concept with data wins
    df = pd.DataFrame(rows, columns=["ticker", "metric", "period_end", "fiscal_year", "value",
                                     "unit", "available_date", "form"])
    if df.empty:
        return df
    df = df.sort_values("available_date")
    if first_filed_only:
        df = df.drop_duplicates(["ticker", "metric", "period_end"], keep="first")
    return df.sort_values(["metric", "period_end"]).reset_index(drop=True)


class SecEdgarClient:
    def __init__(self, http: HttpClient, user_agent: str) -> None:
        if not user_agent or "@" not in user_agent:
            raise DataSourceError(
                "SEC requires a User-Agent with contact info, e.g. 'Jane Doe jane@example.com'. "
                "Set SEC_USER_AGENT in .env")
        self.http = http
        self.http.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._cik_map: dict[str, int] | None = None

    def cik_for(self, ticker: str) -> int:
        if self._cik_map is None:
            payload = self.http.get_json(TICKER_MAP_URL).payload
            self._cik_map = {v["ticker"].upper(): int(v["cik_str"]) for v in payload.values()}
        try:
            return self._cik_map[ticker.upper()]
        except KeyError as exc:
            raise DataSourceError(f"Ticker {ticker} not found in SEC ticker map") from exc

    def fundamentals(self, ticker: str) -> Dataset:
        url = FACTS_URL.format(cik=self.cik_for(ticker))
        resp = self.http.get_json(url)
        df = parse_company_facts(resp.payload, ticker.upper())
        return Dataset("fundamentals", df, Provenance(
            source="sec_edgar_xbrl", url=url,
            notes="As-first-reported annual values; available_date = SEC filing date."))
