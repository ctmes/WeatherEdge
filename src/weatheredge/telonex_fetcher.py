"""
Telonex price fetcher — fallback for when CLOB prices-history has no data.

Telonex (api.telonex.io) stores per-day Polymarket trade parquets going back
to market creation, providing tick-level price coverage for ~39% of 2024-2025
weather markets where the CLOB API's rolling retention window has expired.

Usage (internal):
    price = await fetch_telonex_price_at_time(
        token_id="123...",
        target_dt=datetime(2024, 11, 15, 18, 0),
        condition_id="0xabc...",
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any

import httpx

from weatheredge.cache import cache_key as _cache_key, read_cache, write_cache
from weatheredge.config import TELONEX_API_KEY, TELONEX_BASE

log = logging.getLogger("weatheredge.telonex_fetcher")

_SUBDIR = "telonex_cache"

# Markets metadata cache: loaded once per process, refreshed every 24h.
_markets_cache: dict[str, Any] | None = None   # {market_id -> slug, asset_id_0 -> slug}
_markets_loaded_at: float = 0.0
_MARKETS_TTL = 86_400  # 24 h

# Lock to prevent concurrent downloads (thundering herd)
_markets_lock: asyncio.Lock | None = None


def _get_markets_lock() -> asyncio.Lock:
    global _markets_lock
    if _markets_lock is None:
        _markets_lock = asyncio.Lock()
    return _markets_lock

# Semaphore: be polite to the Telonex API (2 concurrent requests max)
_sem: asyncio.Semaphore | None = None


def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(2)
    return _sem


# ---------------------------------------------------------------------------
# Markets metadata
# ---------------------------------------------------------------------------

async def _load_markets_metadata() -> dict[str, str]:
    """
    Download and cache the Telonex markets parquet.

    Returns two merged dicts:
        market_id  (condition_id hex)  -> slug
        asset_id_0 (YES token id str)  -> slug
    """
    global _markets_cache, _markets_loaded_at

    now = time.monotonic()
    if _markets_cache is not None and (now - _markets_loaded_at) < _MARKETS_TTL:
        return _markets_cache  # type: ignore[return-value]

    # Serialize: only one coroutine downloads; the rest wait and reuse the result.
    async with _get_markets_lock():
        # Re-check after acquiring lock — another coroutine may have populated it.
        now = time.monotonic()
        if _markets_cache is not None and (now - _markets_loaded_at) < _MARKETS_TTL:
            return _markets_cache  # type: ignore[return-value]

        # Try disk cache first (keyed by day so it refreshes daily)
        disk_key = _cache_key("telonex_markets", datetime.utcnow().strftime("%Y-%m-%d"))
        cached_rows = read_cache(_SUBDIR, disk_key, max_age_hours=24)
        if cached_rows is not None:
            _markets_cache = cached_rows
            _markets_loaded_at = now
            return _markets_cache  # type: ignore[return-value]

        log.info("Fetching Telonex markets metadata (one-time download)...")
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(
                    f"{TELONEX_BASE}/datasets/polymarket/markets",
                    headers={"Authorization": f"Bearer {TELONEX_API_KEY}"},
                )
                resp.raise_for_status()
                content = resp.content
        except Exception as e:
            log.warning("Failed to fetch Telonex markets metadata: %s", e)
            _markets_cache = _markets_cache or {}
            return _markets_cache  # type: ignore[return-value]

        try:
            import pandas as pd
            # Only read the 4 columns we need — avoids loading the full parquet
            # schema (which can be 50+ columns) and cuts peak RAM significantly.
            needed_cols = ["slug", "market_id", "asset_id_0", "trades_from"]
            df = pd.read_parquet(BytesIO(content), columns=needed_cols)

            # Only keep rows that have trades data
            has_trades = df["trades_from"].notna() & (df["trades_from"] != "")
            df = df[has_trades]

            lookup: dict[str, str] = {}
            for _, row in df.iterrows():
                slug = row.get("slug", "")
                if not slug:
                    continue
                mid = str(row.get("market_id", "")).strip()
                aid = str(row.get("asset_id_0", "")).strip()
                if mid:
                    lookup[mid.lower()] = slug
                if aid:
                    lookup[aid] = slug

            del df  # free DataFrame memory immediately after building lookup
            write_cache(_SUBDIR, disk_key, lookup)
            _markets_cache = lookup
            _markets_loaded_at = now
            log.info("Telonex markets metadata loaded: %d slug mappings", len(lookup))
            return lookup

        except Exception as e:
            log.warning("Failed to parse Telonex markets parquet: %s", e)
            _markets_cache = _markets_cache or {}
            return _markets_cache  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Per-day trade data
# ---------------------------------------------------------------------------

async def _fetch_day_trades(slug: str, day: str) -> list[dict] | None:
    """
    Fetch and cache Telonex trade records for ``slug`` on ``day`` (YYYY-MM-DD).

    Returns list of {timestamp_us, price} dicts sorted ascending, or None on error.
    """
    cache_k = _cache_key("telonex_trades", slug, day)
    cached = read_cache(_SUBDIR, cache_k)
    if cached is not None:
        return cached  # type: ignore[return-value]

    url = f"{TELONEX_BASE}/downloads/polymarket/trades/{day}"
    params = {"slug": slug, "outcome": "Yes"}
    headers = {"Authorization": f"Bearer {TELONEX_API_KEY}"}

    try:
        async with _get_sem():
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=headers)

        if resp.status_code == 404:
            # No trades on this day — valid empty result
            write_cache(_SUBDIR, cache_k, [])
            return []
        if resp.status_code == 402:
            log.warning("Telonex download limit hit — upgrade plan at telonex.io")
            return None
        resp.raise_for_status()

        import pandas as pd
        df = pd.read_parquet(BytesIO(resp.content))

        rows = [
            {"timestamp_us": int(r["timestamp_us"]), "price": float(r["price"])}
            for _, r in df.iterrows()
            if r.get("price") is not None
        ]
        rows.sort(key=lambda x: x["timestamp_us"])
        write_cache(_SUBDIR, cache_k, rows)
        return rows

    except Exception as e:
        log.warning("Telonex trade fetch failed for %s on %s: %s", slug, day, e)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_telonex_price_at_time(
    token_id: str,
    target_dt: datetime,
    *,
    condition_id: str = "",
) -> float | None:
    """
    Return the last traded YES-side price for this token at or before ``target_dt``.

    Looks up the market slug via ``condition_id`` (preferred) or ``token_id``,
    then fetches the per-day trade parquet from Telonex and walks backward to
    find the most recent price before ``target_dt``.

    Returns ``None`` if no data is available.
    """
    lookup = await _load_markets_metadata()

    # Resolve slug: try condition_id first (more stable), then YES token id
    slug: str | None = None
    cid_lower = condition_id.lower().strip()
    if cid_lower and cid_lower in lookup:
        slug = lookup[cid_lower]
    elif token_id and token_id in lookup:
        slug = lookup[token_id]

    if not slug:
        log.debug(
            "Telonex: no slug found for condition_id=%r token=%r",
            condition_id[:12] if condition_id else "", token_id[:12] if token_id else "",
        )
        return None

    # Fetch the day of target_dt and also the day before (in case target is near midnight)
    days_to_check = [
        (target_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
        target_dt.strftime("%Y-%m-%d"),
    ]

    target_us = int(target_dt.timestamp() * 1_000_000)
    best_price: float | None = None

    for day in days_to_check:
        trades = await _fetch_day_trades(slug, day)
        if not trades:
            continue
        for entry in trades:
            if entry["timestamp_us"] <= target_us:
                best_price = entry["price"]
            else:
                break  # sorted ascending — stop early

    if best_price is not None:
        log.info(
            "Telonex fallback: slug=%s target=%s -> price=%.4f",
            slug, target_dt.date(), best_price,
        )
    else:
        log.debug("Telonex: no trades at or before %s for slug=%s", target_dt, slug)

    return best_price
