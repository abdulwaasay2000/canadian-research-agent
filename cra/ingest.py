"""Ingestion orchestrator / CLI.

  python -m cra.ingest --synthetic                # offline demo (clearly labelled)
  python -m cra.ingest --live --universe config/universe_real.csv
  python -m cra.ingest --check-providers          # connectivity + config check

Live mode pulls whatever providers are configured. A provider that fails is logged in
ingestion_log with status='error' and the run continues; nothing is replaced with
made-up numbers.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from cra.config import Settings, get_settings
from cra.http import DataSourceError, HttpClient
from cra.models import Dataset
from cra import synthetic
from cra.bank_of_canada import BankOfCanadaClient
from cra.eodhd import EodhdClient
from cra.fred import FredClient
from cra.sec_edgar import SecEdgarClient
from cra.validation import validate_fundamentals, validate_prices, validate_series
from cra.store import Warehouse

log = logging.getLogger("cra.ingest")
PKG = Path(__file__).resolve().parent
DEFAULT_UNIVERSE = PKG / "universe.csv"


def _store(wh: Warehouse, ds: Dataset) -> int:
    validators = {"prices": validate_prices, "fx": validate_series, "macro": validate_series,
                  "fundamentals": validate_fundamentals}
    issues = validators[ds.kind](ds.frame)
    wh.log_issues(ds.kind, issues)
    for i in issues:
        (log.error if i.severity == "error" else log.warning)("[%s] %s %s: %s", ds.kind, i.check, i.subject, i.detail)
    n = wh.upsert(ds)
    log.info("Stored %d %s rows from %s%s", n, ds.kind, ds.provenance.source,
             " (SYNTHETIC)" if ds.provenance.is_synthetic else "")
    return n


def _client(settings: Settings, name: str, headers: dict | None = None) -> HttpClient:
    return HttpClient(name, settings.cache_dir, min_interval_s=settings.rate_limits.get(name, 0.5),
                      timeout_s=settings.http_timeout_s, max_retries=settings.http_max_retries,
                      cache_ttl_s=settings.http_cache_ttl_s, headers=headers)


def ingest_synthetic(wh: Warehouse, universe_path: Path = DEFAULT_UNIVERSE) -> None:
    uni = synthetic.load_universe(universe_path)
    wh.replace_universe(uni)
    fundamentals = synthetic.synthetic_fundamentals(uni)
    prices = synthetic.anchor_prices_to_fundamentals(synthetic.synthetic_prices(uni), fundamentals)
    for ds in (prices, synthetic.synthetic_fx(), synthetic.synthetic_macro(), fundamentals):
        _store(wh, ds)


def ingest_live(wh: Warehouse, settings: Settings, universe_path: Path, start: date) -> dict[str, str]:
    status: dict[str, str] = {}
    uni = synthetic.load_universe(universe_path)
    wh.replace_universe(uni)

    def attempt(name: str, kind: str, fn):
        try:
            _store(wh, fn())
            status[name] = "ok"
        except DataSourceError as exc:
            log.error("%s failed: %s", name, exc)
            wh.log_run(kind, name, 0, "error", str(exc))
            status[name] = f"error: {exc}"

    boc = BankOfCanadaClient(_client(settings, "boc"))
    attempt("boc_fx", "fx", lambda: boc.fx(start=max(start, date(2017, 1, 3))))
    attempt("boc_macro", "macro", lambda: boc.macro(start=start))

    if settings.fred_api_key:
        fred = FredClient(_client(settings, "fred"), settings.fred_api_key)
        attempt("fred", "macro", lambda: fred.series(start=start))
    else:
        status["fred"] = "skipped: FRED_API_KEY not set"

    real = uni[~uni["is_synthetic"]]
    if settings.eodhd_api_key:
        eod = EodhdClient(_client(settings, "eodhd"), settings.eodhd_api_key)
        for r in real.itertuples():
            attempt(f"eodhd:{r.ticker}", "prices", lambda r=r: eod.prices(r.ticker, r.exchange, start=start))
    else:
        status["eodhd"] = "skipped: EODHD_API_KEY not set (no licensed price feed configured)"

    if settings.sec_user_agent:
        try:
            sec = SecEdgarClient(_client(settings, "sec"), settings.sec_user_agent)
            for r in real[real["country"] == "US"].itertuples():
                attempt(f"sec:{r.ticker}", "fundamentals", lambda r=r: sec.fundamentals(r.ticker))
        except DataSourceError as exc:
            status["sec"] = f"error: {exc}"
    else:
        status["sec"] = "skipped: SEC_USER_AGENT not set"
    return status


def check_providers(settings: Settings) -> dict[str, str]:
    out = {}
    try:
        res = BankOfCanadaClient(_client(settings, "boc")).verify_series()
        out["bank_of_canada"] = "ok" if all(res.values()) else f"missing series: {[k for k, v in res.items() if not v]}"
    except DataSourceError as exc:
        out["bank_of_canada"] = f"unreachable: {exc}"
    out["fred"] = "key set" if settings.fred_api_key else "no key"
    out["eodhd"] = "key set" if settings.eodhd_api_key else "no key"
    out["sec"] = "user agent set" if settings.sec_user_agent else "SEC_USER_AGENT missing"
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CRA data ingestion (research mode only)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--synthetic", action="store_true", help="Load labelled synthetic demo data")
    g.add_argument("--live", action="store_true", help="Pull from configured real providers")
    g.add_argument("--check-providers", action="store_true")
    p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--start", type=date.fromisoformat, default=date(2015, 1, 1))
    args = p.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.check_providers:
        for k, v in check_providers(settings).items():
            print(f"{k:16s} {v}")
        return 0
    wh = Warehouse(settings.duckdb_path)
    try:
        if args.synthetic:
            ingest_synthetic(wh, args.universe)
        else:
            for k, v in ingest_live(wh, settings, args.universe, args.start).items():
                print(f"{k:24s} {v}")
        print(wh.freshness().to_string(index=False))
    finally:
        wh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
