# Canadian Research Agent: Phase 1

A research platform for Canadian and US stocks, ETFs and forex. **Research and paper-trading only.** No code here can place a real order. Nothing it produces is investment, tax or legal advice.

**Phase 1 includes:** a data layer (Bank of Canada, SEC EDGAR, FRED, EODHD, CSV import, labelled synthetic demo data), a DuckDB warehouse with provenance on every row, data-quality checks, seven FX pairs, CAD conversion, exploratory indicators, preliminary Canadian and US screeners with research labels, an ETF view, a FastAPI backend, a Streamlit dashboard, a Markdown report and 47 tests.

See `docs/ARCHITECTURE.md`, `docs/DATA_SOURCES.md` and `docs/ROADMAP.md`, plus an example output in `docs/example_daily_snapshot.md`.

## Quick start: Windows (PowerShell)

Requires Python 3.11 or newer.

```powershell
cd "$HOME\Desktop\trading agenet\canadian-research-agent"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .
copy .env.example .env      # then edit SEC_USER_AGENT (and keys if you have them)

pytest                                          # 47 tests
python -m cra.pipeline.ingest --synthetic       # demo data (clearly labelled)
streamlit run src/cra/dashboard/app.py          # http://localhost:8501
uvicorn cra.api.main:app --reload               # http://localhost:8000/docs
python -m cra.pipeline.report --out reports/daily.md
```

## Quick start: Docker

```bash
docker compose run --rm tests
docker compose run --rm ingest
docker compose up api dashboard
```

## Trackers and decision matrix

- **Dashboard:** the first two pages are **Decision Matrix** (stocks, ETFs and forex scored factor by factor, with a reason for each) and **Trackers** (price changes, 52-week position, 90-day sparklines and alerts).
- **Standalone web app:** `python -m cra.pipeline.build_webapp --out reports/research_desk.html` builds a single HTML file you can open in any browser.
- **Snapshot:** `python -m cra.pipeline.export_snapshot --out reports/snapshot.json`. You can load this file into the hosted web app with its "Load snapshot.json" button.
- **API:** `/trackers` and `/decision-matrix`.
- **Rules:** set in `config/decision_matrix.json`. They haven't been backtested yet, so treat the verdicts as research labels, not trade signals.

## Using real data

1. In `.env`, set `SEC_USER_AGENT="Your Name you@email.com"`. Optionally add `FRED_API_KEY` and `EODHD_API_KEY`.
2. Run `python -m cra.pipeline.ingest --check-providers` to test connectivity and series codes.
3. Run `python -m cra.pipeline.ingest --live --universe config/universe_real.csv --start 2015-01-01`.
   - Without an EODHD key, no equity prices load. The run logs that clearly and never invents prices.
   - Canadian fundamentals: import CSVs with `cra.data.providers.csv_import.import_csv(path, "fundamentals", "sedar_manual")`. Each row needs `available_date`.
4. To clear demo data before going live, delete `data/warehouse.duckdb`.

DuckDB allows one writer at a time, so stop the dashboard and API while ingesting.

## How the preliminary score works

The score is set up in `config/scoring.json`. Each metric is percentile-ranked within its sector peers, falling back to the whole market when a sector has fewer than 3 peers. Each pillar's points are scaled by data coverage, so **missing data earns zero**. Metrics that don't apply (for example EV/EBITDA for banks) are left out instead of penalised.

Labels are `WATCH`, `RESEARCH FURTHER`, `AVOID FOR NOW` and `INSUFFICIENT DATA`. They mark what to research next and aren't trade signals. The weights haven't been validated out of sample yet; that happens in Phase 3.

## API endpoints

`/health`, `/status`, `/universe`, `/screener/{ca|us}`, `/etfs`, `/prices/{ticker}`, `/forex`, `/forex/{base}/{quote}`, `/macro`. All are read-only.
