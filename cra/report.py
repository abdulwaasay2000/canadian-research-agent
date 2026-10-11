"""Generate a Markdown daily research snapshot from the warehouse.

  python -m cra.report --out reports/daily.md
Every number comes from the warehouse; nothing is written by an LLM in Phase 1.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from cra.config import get_settings
from cra.models import utcnow
from cra.fx_research import pair_summary
from cra.screen import run_stock_screen
from cra.store import Warehouse


def _fmt_pct(x) -> str:
    return "n/a" if pd.isna(x) else f"{x * 100:+.1f}%"


def build_report(wh: Warehouse) -> str:
    lines = [f"# Daily research snapshot", f"_Generated {utcnow():%Y-%m-%d %H:%M} UTC · research only, not advice_", ""]
    if wh.any_synthetic():
        lines += ["> **SYNTHETIC DEMO DATA.** Companies and figures below are fictional and exist only to "
                  "demonstrate the pipeline.", ""]
    f = wh.freshness()
    lines += ["## Data freshness", "", "| dataset | source | last observation | rows |", "|---|---|---|---|"]
    lines += [f"| {r.dataset} | {r.source} | {r.last_observation} | {r.rows:,} |" for r in f.itertuples()]

    fx = pair_summary(wh.fx())
    lines += ["", "## Currencies (BoC indicative rates)", "",
              "| pair | last | 1w | 1m | 1y | 3m vol (ann.) | trend state |", "|---|---|---|---|---|---|---|"]
    for r in fx[fx["status"] == "ok"].itertuples():
        lines.append(f"| {r.pair} | {r.last:.4f} | {_fmt_pct(r.chg_1w)} | {_fmt_pct(r.chg_1m)} | "
                     f"{_fmt_pct(r.chg_1y)} | {r.vol_3m_ann * 100:.1f}% | {r.trend_state} |")
    lines += ["", "_Trend state describes price vs. 50/200-day averages. It is not a forecast._"]

    for market, title in (("CA", "Canadian stocks"), ("US", "US stocks")):
        df = run_stock_screen(wh, market)
        lines += ["", f"## {title}: preliminary screen", ""]
        if df.empty:
            lines.append("No stocks loaded.")
            continue
        counts = df["label"].value_counts().to_dict()
        lines.append("Label counts: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        lines += ["", "| ticker | sector | score / max | coverage | label | P/E | ROE |", "|---|---|---|---|---|---|---|"]
        for r in df.itertuples():
            pe = "n/a" if pd.isna(r.pe) else f"{r.pe:.1f}"
            lines.append(f"| {r.ticker} | {r.sector} | {r.score:.1f} / {r.max_possible:.0f} | {r.coverage:.0%} | "
                         f"{r.label} | {pe} | {_fmt_pct(r.roe)} |")
        if "WATCH" not in counts:
            lines.append("\nNo company met the WATCH threshold today. No candidates were forced into the list.")

    issues = wh.recent_issues(20)
    lines += ["", "## Data quality warnings", ""]
    lines += [f"- [{r.severity}] {r.kind} · {r.check_name} · {r.subject}: {r.detail}" for r in issues.itertuples()] \
        or ["- None"]
    lines += ["", "## Limits of this snapshot", "",
              "- Scores are a preliminary peer-ranking, not validated out of sample yet (Phase 3).",
              "- Competitive position is not auto-scored; maximum possible score is 90.",
              "- FX rates are daily indicative rates with no bid/ask spread."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("reports/daily_snapshot.md"))
    a = p.parse_args(argv)
    wh = Warehouse(get_settings().duckdb_path, read_only=True)
    try:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(build_report(wh), encoding="utf-8")
    finally:
        wh.close()
    print(f"Wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
