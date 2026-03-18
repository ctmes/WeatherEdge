"""
Shared disk cache utility for WeatherEdge.

Provides deterministic, TTL-aware JSON caching under ~/.weatheredge/<subdir>/.
All modules that make external API calls should use this instead of
copy-pasting their own _cache_key / _read_cache / _write_cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("weatheredge.cache")

_BASE_DIR = Path.home() / ".weatheredge"


def cache_key(*parts: str) -> str:
    """Deterministic filename from key components."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16] + ".json"


def read_cache(subdir: str, key: str, *, max_age_hours: float | None = None) -> Any | None:
    """
    Read a cached JSON value.

    subdir:         Cache subdirectory name (e.g. "forecast_cache")
    key:            Filename returned by cache_key()
    max_age_hours:  If set, reject entries older than this many hours.
                    If None, cache never expires (suitable for historical data).
    """
    path = _BASE_DIR / subdir / key
    if not path.exists():
        return None

    if max_age_hours is not None:
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours > max_age_hours:
            return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Cache file %s is unreadable or corrupt (%s) — treating as cache miss.", path, e)
        return None


def write_cache(subdir: str, key: str, data: Any) -> None:
    """Write a JSON-serialisable value to the disk cache."""
    directory = _BASE_DIR / subdir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / key
    path.write_text(json.dumps(data, default=str), encoding="utf-8")


def cache_fingerprint(subdir: str) -> str:
    """Hash of all cached files in a subdirectory — identifies the exact data snapshot."""
    directory = _BASE_DIR / subdir
    if not directory.exists():
        return "empty"
    files = sorted(directory.glob("*.json"))
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        st = f.stat()
        h.update(str(st.st_mtime).encode())
        h.update(str(st.st_size).encode())
    return h.hexdigest()[:16]
