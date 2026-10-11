#!/usr/bin/env sh
# Web start script (Render, Railway, Fly, any Docker host).
# Loads data on first boot, then serves the dashboard on $PORT.
set -e
export PYTHONPATH="${PYTHONPATH:-$(pwd)}"
PORT="${PORT:-8501}"
DB="${CRA_DUCKDB_PATH:-${CRA_DATA_DIR:-./data}/warehouse.duckdb}"

if [ "${CRA_DATA_SOURCE:-synthetic}" = "live" ]; then
  echo "Refreshing live data..."
  python -m cra.ingest --live --universe "${CRA_UNIVERSE:-cra/universe_real.csv}" || echo "Live ingest had errors (see log)."
elif [ ! -f "$DB" ]; then
  echo "No warehouse found - loading labelled demo data."
  python -m cra.ingest --synthetic
fi

exec streamlit run cra/dashboard.py \
  --server.address=0.0.0.0 --server.port="$PORT" --server.headless=true \
  --browser.gatherUsageStats=false
