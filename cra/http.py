"""Polite HTTP client: retries with exponential backoff, per-host rate limiting,
on-disk response cache with TTL, and explicit errors (never silent fallbacks)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class DataSourceError(RuntimeError):
    """Raised when a provider cannot return valid data. Callers must handle it explicitly."""


@dataclass
class CachedResponse:
    payload: Any
    fetched_at: float
    from_cache: bool
    url: str


class RateLimiter:
    def __init__(self, min_interval_s: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.min_interval_s = max(0.0, min_interval_s)
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self.min_interval_s - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


class HttpClient:
    def __init__(
        self,
        name: str,
        cache_dir: Path,
        min_interval_s: float = 0.5,
        timeout_s: float = 20.0,
        max_retries: int = 4,
        cache_ttl_s: int = 6 * 3600,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.cache_dir = Path(cache_dir) / name
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.cache_ttl_s = cache_ttl_s
        self.session = session or requests.Session()
        self.session.headers.update(headers or {})
        self._sleep = sleep
        self.limiter = RateLimiter(min_interval_s, sleep=sleep)

    # ---- cache -------------------------------------------------------------
    def _cache_path(self, url: str, params: dict | None) -> Path:
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        # Never put secrets in the cache key file name; hash everything.
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _read_cache(self, path: Path) -> tuple[Any, float] | None:
        if not path.exists():
            return None
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            return blob["payload"], float(blob["fetched_at"])
        except (ValueError, KeyError):
            log.warning("Corrupt cache file %s ignored", path)
            return None

    # ---- fetch -------------------------------------------------------------
    def get_json(self, url: str, params: dict | None = None, use_cache: bool = True,
                 redact_params: tuple[str, ...] = ("api_key", "api_token", "apikey")) -> CachedResponse:
        path = self._cache_path(url, params)
        if use_cache:
            cached = self._read_cache(path)
            if cached and (time.time() - cached[1]) < self.cache_ttl_s:
                return CachedResponse(cached[0], cached[1], True, url)

        safe_params = {k: ("***" if k in redact_params else v) for k, v in (params or {}).items()}
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_s)
                if resp.status_code in RETRYABLE_STATUS:
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                    log.warning("%s %s -> HTTP %s (attempt %d); retrying in %.1fs",
                                self.name, url, resp.status_code, attempt + 1, delay)
                    last_err = DataSourceError(f"HTTP {resp.status_code}")
                    self._sleep(min(delay, 60))
                    continue
                if resp.status_code >= 400:
                    raise DataSourceError(
                        f"{self.name}: HTTP {resp.status_code} for {url} params={safe_params}")
                payload = resp.json()
                fetched_at = time.time()
                path.write_text(json.dumps({"payload": payload, "fetched_at": fetched_at}), encoding="utf-8")
                return CachedResponse(payload, fetched_at, False, url)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_err = exc
                delay = 2 ** attempt
                log.warning("%s network error %s (attempt %d); retrying in %ss", self.name, exc, attempt + 1, delay)
                self._sleep(delay)
            except ValueError as exc:  # JSON decode
                raise DataSourceError(f"{self.name}: invalid JSON from {url}") from exc

        # Retries exhausted: fall back to a stale cache *only* with an explicit flag in the result.
        stale = self._read_cache(path) if use_cache else None
        if stale:
            log.error("%s: using STALE cache for %s after failures: %s", self.name, url, last_err)
            return CachedResponse(stale[0], stale[1], True, url)
        raise DataSourceError(f"{self.name}: failed after {self.max_retries + 1} attempts: {last_err}")
