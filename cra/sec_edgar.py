"""SEC EDGAR XBRL client (US GAAP and IFRS) (free, no key; a descriptive User-Agent with contact email is required
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

# Our metric name -> ordered list of (taxonomy, concept) to try. The first concept with data wins.
# US-GAAP covers US filers and Canadian filers that report in US GAAP (e.g. CN Rail, Enbridge).
# IFRS (ifrs-full) covers most Canadian 40-F filers, e.g. the big banks.
CONCEPTS: dict[str, list[tuple[str, str]]] = {
    "revenue": [("us-gaap", "Revenues"), ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                ("us-gaap", "SalesRevenueNet"), ("ifrs-full", "Revenue"),
                ("ifrs-full", "RevenueFromContractsWithCustomers")],
    "net_income": [("us-gaap", "NetIncomeLoss"), ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
                   ("ifrs-full", "ProfitLoss")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss"), ("ifrs-full", "ProfitLossFromOperatingActivities")],
    "eps_diluted": [("us-gaap", "EarningsPerShareDiluted"), ("ifrs-full", "DilutedEarningsLossPerShare")],
    "operating_cash_flow": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
                            ("ifrs-full", "CashFlowsFromUsedInOperatingActivities")],
    "capex": [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
              ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
              ("ifrs-full", "PurchaseOfPropertyPlantAndEquipment")],
    "total_assets": [("us-gaap", "Assets"), ("ifrs-full", "Assets")],
    "total_liabilities": [("us-gaap", "Liabilities"), ("ifrs-full", "Liabilities")],
    "shareholders_equity": [("us-gaap", "StockholdersEquity"), ("ifrs-full", "EquityAttributableToOwnersOfParent"),
                            ("ifrs-full", "Equity")],
    "current_assets": [("us-gaap", "AssetsCurrent"), ("ifrs-full", "CurrentAssets")],
    "current_liabilities": [("us-gaap", "LiabilitiesCurrent"), ("ifrs-full", "CurrentLiabilities")],
    "cash": [("us-gaap", "CashAndCashEquivalentsAtCarryingValue"), ("ifrs-full", "CashAndCashEquivalents")],
    "long_term_debt": [("us-gaap", "LongTermDebtNoncurrent"), ("us-gaap", "LongTermDebt"),
                       ("ifrs-full", "NoncurrentPortionOfNoncurrentBorrowings"),
                       ("ifrs-full", "LongtermBorrowings")],
    "interest_expense": [("us-gaap", "InterestExpense"), ("us-gaap", "InterestExpenseNonoperating"),
                         ("ifrs-full", "FinanceCosts"), ("ifrs-full", "InterestExpense")],
    "shares_diluted": [("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
                       ("ifrs-full", "AdjustedWeightedAverageShares")],
    "depreciation_amortization": [("us-gaap", "DepreciationDepletionAndAmortization"),
                                  ("us-gaap", "DepreciationAndAmortization"),
                                  ("ifrs-full", "DepreciationAndAmortisationExpense"),
                                  ("ifrs-full", "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss")],
}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


def parse_company_facts(payload: dict, ticker: str, first_filed_only: bool = True) -> pd.DataFrame:
    facts = payload.get("facts") or {}
    rows = []
    for metric, concepts in CONCEPTS.items():
        found = False
        for taxonomy, concept in concepts:
            node = (facts.get(taxonomy) or {}).get(concept)
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
                    found = True
            if found:
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

    def fundamentals(self, ticker: str, label: str | None = None) -> Dataset:
        """ticker: the symbol SEC knows (US listing). label: the ticker to store rows under
        (e.g. TSX symbol CNR for SEC ticker CNI)."""
        url = FACTS_URL.format(cik=self.cik_for(ticker))
        resp = self.http.get_json(url)
        df = parse_company_facts(resp.payload, (label or ticker).upper())
        return Dataset("fundamentals", df, Provenance(
            source="sec_edgar_xbrl", url=url,
            notes="As-first-reported annual values; available_date = SEC filing date."))
