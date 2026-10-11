"""Export trackers + decision matrix to one JSON file (feeds the web app / sharing).

  python -m cra.export_snapshot --out reports/snapshot.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from cra import __version__
from cra.config import get_settings
from cra.models import utcnow
from cra.matrix import build_all, load_config
from cra.store import Warehouse
from cra.tracker import alerts, asset_trackers, fx_trackers, rate_trackers


def _clean(v):
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (pd.Timestamp,)):
        return v.isoformat()
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if v is pd.NaT:
        return None
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict("records")]


def build_snapshot(wh: Warehouse) -> dict:
    assets, fx = asset_trackers(wh), fx_trackers(wh.fx())
    matrix = build_all(wh)
    fresh = wh.freshness()
    return {
        "generated_at": utcnow().isoformat(), "app_version": __version__,
        "matrix_rules_version": load_config()["version"],
        "rules": load_config(),
        "synthetic": wh.any_synthetic(),
        "freshness": records(fresh),
        "trackers": {"assets": records(assets), "fx": records(fx), "rates": records(rate_trackers(wh.macro()))},
        "alerts": records(alerts(assets, fx)),
        "matrix": {k: records(v) for k, v in matrix.items()},
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("reports/snapshot.json"))
    a = p.parse_args(argv)
    wh = Warehouse(get_settings().duckdb_path, read_only=True)
    try:
        snap = build_snapshot(wh)
    finally:
        wh.close()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    print(f"Wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
