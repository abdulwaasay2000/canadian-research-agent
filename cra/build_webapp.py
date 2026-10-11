"""Build the standalone web app (trackers + decision matrix) from the warehouse.

  python -m cra.build_webapp --out reports/research_desk.html
Open the file in any browser. No server needed; the data snapshot is embedded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cra.config import get_settings
from cra.export_snapshot import build_snapshot
from cra.store import Warehouse

TEMPLATE = Path(__file__).resolve().parent / "template.html"


def render(snapshot: dict, standalone: bool = True) -> str:
    data = json.dumps(snapshot, separators=(",", ":")).replace("</", "<\\/")
    body = TEMPLATE.read_text(encoding="utf-8").replace("/*__SNAPSHOT__*/", data)
    if not standalone:
        return body
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            "<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n"
            + body + "\n</body></html>\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("reports/research_desk.html"))
    p.add_argument("--fragment", action="store_true", help="omit <html>/<head> wrapper (for hosted publishing)")
    a = p.parse_args(argv)
    wh = Warehouse(get_settings().duckdb_path, read_only=True)
    try:
        snap = build_snapshot(wh)
    finally:
        wh.close()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render(snap, standalone=not a.fragment), encoding="utf-8")
    print(f"Wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
