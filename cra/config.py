"""Environment-based configuration. No secrets live in code.

All settings come from environment variables (optionally loaded from a .env file).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars always win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cache_dir: Path
    duckdb_path: Path
    log_level: str
    # Mode guard: Phase 1 only supports research. Live trading is not implemented anywhere.
    mode: str
    use_synthetic_data: bool
    # Network / politeness
    http_timeout_s: float
    http_max_retries: int
    http_cache_ttl_s: int
    # Provider credentials (optional; providers without a key are skipped)
    sec_user_agent: str
    fred_api_key: str | None
    eodhd_api_key: str | None
    # Per-provider minimum seconds between requests
    rate_limits: dict[str, float] = field(default_factory=dict)

    @property
    def is_research_only(self) -> bool:
        return self.mode in {"research", "paper"}


def get_settings(env_file: str | Path | None = None) -> Settings:
    root = Path(os.environ.get("CRA_HOME", Path.cwd()))
    _load_dotenv(Path(env_file) if env_file else root / ".env")

    data_dir = Path(os.environ.get("CRA_DATA_DIR", root / "data"))
    mode = os.environ.get("CRA_MODE", "research").lower()
    if mode not in {"research", "paper"}:
        raise ValueError(
            f"CRA_MODE={mode!r} is not allowed. Only 'research' and 'paper' exist; "
            "real-money execution is deliberately not implemented."
        )
    return Settings(
        data_dir=data_dir,
        cache_dir=Path(os.environ.get("CRA_CACHE_DIR", data_dir / "cache")),
        duckdb_path=Path(os.environ.get("CRA_DUCKDB_PATH", data_dir / "warehouse.duckdb")),
        log_level=os.environ.get("CRA_LOG_LEVEL", "INFO"),
        mode=mode,
        use_synthetic_data=_bool("CRA_USE_SYNTHETIC_DATA", True),
        http_timeout_s=float(os.environ.get("CRA_HTTP_TIMEOUT_S", "20")),
        http_max_retries=int(os.environ.get("CRA_HTTP_MAX_RETRIES", "4")),
        http_cache_ttl_s=int(os.environ.get("CRA_HTTP_CACHE_TTL_S", "21600")),
        sec_user_agent=os.environ.get("SEC_USER_AGENT", ""),
        fred_api_key=os.environ.get("FRED_API_KEY") or None,
        eodhd_api_key=os.environ.get("EODHD_API_KEY") or None,
        rate_limits={
            # SEC's fair-access guidance is max 10 req/s; we stay well under it.
            "sec": float(os.environ.get("CRA_RATE_SEC_S", "0.2")),
            "boc": float(os.environ.get("CRA_RATE_BOC_S", "0.5")),
            "fred": float(os.environ.get("CRA_RATE_FRED_S", "0.6")),
            "eodhd": float(os.environ.get("CRA_RATE_EODHD_S", "0.1")),
        },
    )
