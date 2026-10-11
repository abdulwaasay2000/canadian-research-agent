"""Transparent, peer-normalised preliminary research score.

How a pillar is scored (fully reproducible):
  1. Each metric is percentile-ranked (0..1) within the company's sector peer group, flipped
     when lower is better. If fewer than `min_peers` companies have the metric, the whole
     market's stocks are used instead and the row is flagged `peer_fallback`.
  2. pillar_points = pillar_weight x mean(percentiles of available metrics)
                     x (available metrics / applicable metrics)
     -> a missing metric earns nothing; a not-applicable metric (e.g. EV/EBITDA for a bank)
        is removed from the denominator.
  3. total = sum of pillar points (max 100). `coverage` = share of applicable metric weight
     that had data. Pillars that cannot be automated (competitive position) contribute 0
     and lower `max_possible`.

Labels (WATCH / RESEARCH FURTHER / AVOID FOR NOW / INSUFFICIENT DATA) are research triage
labels, never trade instructions.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_CONFIG = Path(__file__).resolve().parent / "scoring.json"


def load_config(path: str | Path | None = None) -> dict:
    return json.loads(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))


def _percentiles(values: pd.Series, higher_is_better: bool) -> pd.Series:
    v = pd.to_numeric(values, errors="coerce")
    n = int(v.notna().sum())
    if n == 0:
        return pd.Series(np.nan, index=v.index)
    if n == 1:  # a lone value carries no relative information
        return pd.Series(0.5, index=v.index).where(v.notna())
    ranks = (v if higher_is_better else -v).rank(method="average")
    return ((ranks - 1) / (n - 1)).where(v.notna())  # 0 = worst peer, 1 = best peer


def score_universe(metrics: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """metrics: one row per company with columns ticker, sector, country + metric columns.
    `not_applicable` is a comma-separated list of metrics excluded for that company's sector;
    any other NaN metric counts as missing."""
    cfg = config or load_config()
    min_peers = cfg.get("min_peers", 3)
    df = metrics.copy().reset_index(drop=True)
    df["peer_fallback"] = False
    total = pd.Series(0.0, index=df.index)
    max_possible = pd.Series(0.0, index=df.index)
    applicable_w = pd.Series(0.0, index=df.index)
    available_w = pd.Series(0.0, index=df.index)

    for pillar, spec in cfg["pillars"].items():
        pts, mets = float(spec["points"]), spec["metrics"]
        if not mets:
            df[f"{pillar}_pts"] = np.nan  # not automated
            continue
        pct_cols = {}
        for m, higher in mets.items():
            if m not in df:
                df[m] = np.nan
            col = pd.Series(np.nan, index=df.index)
            for _, idx in df.groupby("sector").groups.items():
                vals = pd.to_numeric(df.loc[idx, m], errors="coerce")
                if vals.notna().sum() >= min_peers:
                    col.loc[idx] = _percentiles(vals, higher)
                else:
                    market_vals = pd.to_numeric(df[m], errors="coerce")
                    p = _percentiles(market_vals, higher)
                    col.loc[idx] = p.loc[idx]
                    df.loc[idx[vals.notna().to_numpy()], "peer_fallback"] = True
            pct_cols[m] = col
        pct = pd.DataFrame(pct_cols)
        na_sets = df.get("not_applicable", pd.Series("", index=df.index)).fillna("").map(
            lambda x: set(filter(None, str(x).split(","))))
        applicable = pd.DataFrame({m: na_sets.map(lambda s, m=m: m not in s) for m in mets})
        pct = pct.where(applicable)
        n_app = applicable.sum(axis=1)
        n_avail = pct.notna().sum(axis=1)
        pillar_pts = pts * pct.mean(axis=1).fillna(0) * (n_avail / n_app.replace(0, np.nan)).fillna(0)
        df[f"{pillar}_pts"] = pillar_pts.round(2)
        has_app = n_app > 0
        total += pillar_pts
        max_possible += np.where(has_app, pts, 0)
        applicable_w += np.where(has_app, pts, 0)
        available_w += np.where(has_app, pts * n_avail / n_app.replace(0, np.nan), 0)

    df["score"] = total.round(1)
    df["max_possible"] = max_possible
    df["coverage"] = (available_w / applicable_w.replace(0, np.nan)).round(2)
    df["score_pct_of_possible"] = (100 * total / max_possible.replace(0, np.nan)).round(1)
    df["label"] = df.apply(lambda r: _label(r, cfg), axis=1)
    df["model_version"] = cfg.get("version", "unknown")
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def _label(row: pd.Series, cfg: dict) -> str:
    if pd.isna(row["coverage"]) or row["coverage"] < cfg.get("min_coverage_for_label", 0.6):
        return "INSUFFICIENT DATA"
    pct = row["score_pct_of_possible"]
    if pct >= cfg["labels"]["watch"]:
        return "WATCH"
    if pct >= cfg["labels"]["research_further"]:
        return "RESEARCH FURTHER"
    return "AVOID FOR NOW"
