"""
pmxt archive fetcher — stream historical Polymarket order book data
from the pmxt Parquet archive (https://archive.pmxt.dev) for backtesting.

Strategy: the archive contains raw order book snapshots keyed by hex
market_id (condition_id).  To find weather markets, we first fetch
market metadata from Gamma, then look up the matching order book
snapshots in the archive to get real historical bid/ask prices.

Usage (standalone probe):
    python -m weatheredge probe-pmxt
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from weatheredge.config import (
    PMXT_ARCHIVE_BASE,
    PMXT_WEATHER_SLUGS,
    NON_WEATHER_BLOCKLIST,
)
from weatheredge.models import ParsedMarket

log = logging.getLogger("weatheredge.pmxt_fetcher")


# ---------------------------------------------------------------------------
# HTTP download → Polars (bypasses object-store Content-Length issue)
# ---------------------------------------------------------------------------


def _download_parquet(url: str, *, timeout: int = 120) -> pl.DataFrame:
    """Download a Parquet file via httpx and read it with Polars.

    Polars' native HTTP reader uses the object-store crate which requires
    a Content-Length header.  The pmxt server doesn't always provide one,
    so we download the bytes with httpx first.
    """
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return pl.read_parquet(io.BytesIO(resp.content))


# ---------------------------------------------------------------------------
# Disk cache (mirrors backtest_fetchers pattern)
# ---------------------------------------------------------------------------

_CACHE_DIR = Path.home() / ".weatheredge" / "pmxt_cache"


def _cache_key(*parts: str) -> str:
    """Deterministic filename from key components."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16] + ".parquet"


def _read_cache(key: str) -> pl.DataFrame | None:
    path = _CACHE_DIR / key
    if path.exists():
        try:
            return pl.read_parquet(path)
        except Exception:
            return None
    return None


def _write_cache(key: str, df: pl.DataFrame) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / key
    df.write_parquet(path)


# ---------------------------------------------------------------------------
# URL generation
# ---------------------------------------------------------------------------


def build_date_urls(start: date, end: date) -> list[tuple[date, str]]:
    """
    Generate pmxt archive URLs for each day in [start, end].

    The actual archive lives on Cloudflare R2 with hourly files:
    https://r2.pmxt.dev/polymarket_orderbook_YYYY-MM-DDTHH.parquet

    We pick one snapshot per day (hour 12 UTC as a midday representative). 
    """
    urls: list[tuple[date, str]] = []
    current = start
    while current <= end:
        # Use hour 12 (midday UTC) as the representative snapshot
        url = (
            f"{PMXT_ARCHIVE_BASE}"
            f"/polymarket_orderbook_{current.isoformat()}T12.parquet"
        )
        urls.append((current, url))
        current += timedelta(days=1)
    return urls


def build_hourly_urls(target_date: date) -> list[str]:
    """
    Generate all 24 hourly URLs for a single day (for fallback / full-day scans).
    """
    return [
        f"{PMXT_ARCHIVE_BASE}/polymarket_orderbook_{target_date.isoformat()}T{h:02d}.parquet"
        for h in range(24)
    ]


# ---------------------------------------------------------------------------
# Schema probe (debug helper)
# ---------------------------------------------------------------------------


def probe_schema(sample_date: date | None = None) -> dict[str, str]:
    """
    Download a small sample Parquet file and return {column: dtype}.

    Useful for debugging since the archive schema isn't fully documented.
    """
    if sample_date is None:
        sample_date = date(2026, 3, 3)  # known recent file

    url = build_date_urls(sample_date, sample_date)[0][1]
    log.info("Probing pmxt schema from %s", url)

    try:
        df = _download_parquet(url)
        schema = {col: str(dtype) for col, dtype in zip(df.columns, df.dtypes)}
        log.info("pmxt schema (%d cols, %d rows): %s", len(schema), len(df), schema)
        return schema
    except Exception as e:
        log.error("Failed to probe pmxt archive at %s: %s", url, e)
        return {}


# ---------------------------------------------------------------------------
# Weather slug detection (kept for utility)
# ---------------------------------------------------------------------------


def _is_weather_slug(text: str) -> bool:
    """Check if text contains any weather-related slug keywords."""
    text_lower = text.lower()
    # Reject non-weather topics first
    for block in NON_WEATHER_BLOCKLIST:
        if block in text_lower:
            return False
    # Check for weather keywords
    return any(kw in text_lower for kw in PMXT_WEATHER_SLUGS)


# ---------------------------------------------------------------------------
# Fetch order book snapshots for specific market IDs
# ---------------------------------------------------------------------------


def fetch_orderbook_at_timestamp(
    target_ts: datetime,
    market_ids: set[str],
) -> dict[str, dict]:
    """
    Download the pmxt hourly file matching target_ts and extract the latest 
    book_snapshot for each market_id that occurred BEFORE target_ts.
    """
    hour_str = target_ts.strftime("%H")
    date_str = target_ts.date().isoformat()
    
    cache_k = _cache_key("orderbook_precise", date_str, hour_str,
                         hashlib.sha256(",".join(sorted(market_ids)).encode()).hexdigest()[:8])
    cached = _read_cache(cache_k)
    if cached is not None:
        result: dict[str, dict] = {}
        for row in cached.iter_rows(named=True):
            result[row["market_id"]] = json.loads(row["data"])
        return result

    url = f"{PMXT_ARCHIVE_BASE}/polymarket_orderbook_{date_str}T{hour_str}.parquet"
    log.info("Fetching precise pmxt orderbook for %s ...", target_ts.isoformat())

    try:
        df = _download_parquet(url)
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return {}

    if df.is_empty():
        return {}

    # Filter for our market IDs, book_snapshot events, and timestamp <= target_ts
    # pmxt timestamps are usually in milliseconds or microseconds. 
    # We'll assume the df 'timestamp_received' is what we filter on.
    target_ms = int(target_ts.timestamp() * 1000)
    
    filtered = df.filter(
        pl.col("market_id").is_in(list(market_ids))
        & (pl.col("update_type") == "book_snapshot")
        & (pl.col("timestamp_received") <= target_ms)
    )

    if filtered.is_empty():
        return {}

    # Take the latest snapshot per market
    filtered = filtered.sort("timestamp_received", descending=True)

    result: dict[str, dict] = {}
    seen: set[str] = set()

    for row in filtered.iter_rows(named=True):
        mid = row["market_id"]
        if mid in seen:
            continue
        seen.add(mid)
        try:
            data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
            result[mid] = data
        except (json.JSONDecodeError, TypeError):
            continue

    # Cache the filtered result
    if result:
        cache_rows = [{"market_id": mid, "data": json.dumps(d)} for mid, d in result.items()]
        cache_df = pl.DataFrame(cache_rows)
        _write_cache(cache_k, cache_df)
    return result


def fetch_orderbook_for_markets(
    target_date: date,
    market_ids: set[str],
) -> dict[str, dict]:
    """
    Download a pmxt hourly file and extract the latest book_snapshot for
    each market_id in the given set.

    Returns {market_id: {best_bid, best_ask, bids, asks, timestamp}}.
    """
    cache_k = _cache_key("orderbook", target_date.isoformat(),
                         hashlib.sha256(",".join(sorted(market_ids)).encode()).hexdigest()[:8])
    cached = _read_cache(cache_k)
    if cached is not None:
        log.debug("Cache hit for orderbook on %s", target_date)
        # Convert cached DataFrame back to dict
        result: dict[str, dict] = {}
        for row in cached.iter_rows(named=True):
            result[row["market_id"]] = json.loads(row["data"])
        return result

    url = build_date_urls(target_date, target_date)[0][1]
    log.info("Fetching pmxt orderbook for %s ...", target_date)

    try:
        df = _download_parquet(url)
    except Exception as e:
        log.warning("Failed to fetch %s: %s (retrying once...)", url, e)
        time.sleep(2)
        try:
            df = _download_parquet(url)
        except Exception as e2:
            log.warning("Retry failed for %s: %s — skipping", url, e2)
            return {}

    if df.is_empty():
        return {}

    # Filter for our market IDs and book_snapshot events
    filtered = df.filter(
        pl.col("market_id").is_in(list(market_ids))
        & (pl.col("update_type") == "book_snapshot")
    )

    if filtered.is_empty():
        log.debug("No matching book_snapshots for %d market IDs on %s",
                  len(market_ids), target_date)
        return {}

    # Take the latest snapshot per market
    filtered = filtered.sort("timestamp_received", descending=True)

    result: dict[str, dict] = {}
    seen: set[str] = set()

    for row in filtered.iter_rows(named=True):
        mid = row["market_id"]
        if mid in seen:
            continue
        seen.add(mid)
        try:
            data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
            result[mid] = data
        except (json.JSONDecodeError, TypeError):
            continue

    log.info("Found book snapshots for %d / %d weather markets on %s",
             len(result), len(market_ids), target_date)

    # Cache the filtered result
    if result:
        cache_rows = [{"market_id": mid, "data": json.dumps(d)} for mid, d in result.items()]
        cache_df = pl.DataFrame(cache_rows)
        _write_cache(cache_k, cache_df)

    return result


def enrich_market_with_orderbook(
    parsed: ParsedMarket,
    orderbook: dict | None = None,
    per_bin_orderbooks: dict[int, dict] | None = None,
) -> ParsedMarket:
    """
    Replace post-resolution prices on a ParsedMarket with real historical
    bid/ask midpoint from the pmxt order book snapshot.

    For binary markets (2 outcomes): uses a single orderbook dict.
    For multi-outcome (daily events): uses per_bin_orderbooks mapping
    bin_index -> orderbook dict to set each bin's YES midpoint price.
    """
    if per_bin_orderbooks and len(parsed.outcome_prices) > 2:
        # Multi-outcome: set each bin's price from its own order book
        original_prices = len(parsed.outcome_prices)
        new_prices = [0.0] * original_prices
        enriched_count = 0
        for bin_idx, ob in per_bin_orderbooks.items():
            if bin_idx >= original_prices:
                continue
            best_bid = float(ob.get("best_bid") or 0)
            best_ask = float(ob.get("best_ask") or 0)

            if best_bid > 0 and best_ask > 0:
                midpoint = (best_bid + best_ask) / 2.0
            elif best_bid > 0:
                midpoint = best_bid
            elif best_ask > 0:
                midpoint = best_ask
            else:
                continue  # No useful price data for this bin

            # The midpoint from the YES side of the child market
            side = ob.get("side", "YES")
            if side == "YES":
                new_prices[bin_idx] = midpoint
            else:
                new_prices[bin_idx] = 1.0 - midpoint
            enriched_count += 1

        parsed.outcome_prices = new_prices
        if enriched_count > 0:
            log.info(
                "Multi-outcome enrichment: set %d/%d bin prices from order book",
                enriched_count, len(parsed.outcome_prices),
            )
        return parsed

    # Binary market path (original logic)
    if len(parsed.outcome_prices) == 2:
        parsed.outcome_prices = [0.0, 0.0]

    if orderbook is None:
        return parsed

    best_bid = float(orderbook.get("best_bid") or 0)
    best_ask = float(orderbook.get("best_ask") or 0)

    if best_bid > 0 and best_ask > 0:
        midpoint = (best_bid + best_ask) / 2.0
    elif best_bid > 0:
        midpoint = best_bid
    elif best_ask > 0:
        midpoint = best_ask
    else:
        return parsed  # No useful price data

    # For binary markets, replace outcome_prices with [midpoint, 1-midpoint]
    side = orderbook.get("side", "YES")
    if side == "YES":
        yes_price = midpoint
    else:
        yes_price = 1.0 - midpoint

    if len(parsed.outcome_prices) == 2:
        parsed.outcome_prices = [yes_price, 1.0 - yes_price]
    # Multi-outcome with only a single orderbook entry — not enough data,
    # skip rather than inject partial prices

    return parsed


# ---------------------------------------------------------------------------
# Top-level fetcher (matches fetch_resolved_markets signature)
# ---------------------------------------------------------------------------


async def fetch_pmxt_resolved_markets(
    start: date,
    end: date,
    *,
    model_name: str = "ecmwf_ifs",
    latency_seconds: int = 60,
) -> list[tuple[ParsedMarket, dict]]:
    """
    Fetch weather markets from Gamma, then enrich with real historical
    prices from the pmxt archive order book snapshots.
    """
    import asyncio
    from weatheredge.backtest_fetchers import (
        fetch_resolved_markets,
        fetch_resolved_daily_events,
    )
    from weatheredge.config import MODEL_RELEASE_CHART
    from tqdm.asyncio import tqdm

    # Warn if date range predates known archive availability
    PMXT_ARCHIVE_START = date(2026, 2, 22)  # confirmed earliest data
    if start < PMXT_ARCHIVE_START:
        log.warning(
            "pmxt archive may not have data before %s (requested %s). "
            "Order book enrichment may be incomplete — falling back to Gamma prices.",
            PMXT_ARCHIVE_START, start,
        )

    # Step 1: get resolved weather markets from Gamma (for metadata + resolution)
    legacy_pairs = await fetch_resolved_markets(start, end)
    daily_pairs = await fetch_resolved_daily_events(start, end)
    all_pairs = legacy_pairs + daily_pairs

    log.info("Gamma returned %d resolved weather markets for pmxt enrichment", len(all_pairs))

    if not all_pairs:
        return []

    # Step 2: collect all market IDs we need order book data for
    # For multi-outcome (daily events), we need child token IDs, not parent event ID
    market_id_set: set[str] = set()
    # Map: token_id -> (parsed_market_index, bin_index)
    token_to_bin: dict[str, tuple[int, int]] = {}

    for idx, (parsed, raw) in enumerate(all_pairs):
        if "markets" in raw and len(raw["markets"]) > 0:
            # Multi-outcome daily event: PMXT indexes by child condition_id
            sorted_markets = sorted(raw["markets"], key=lambda m: int(m.get("groupItemThreshold", "0")))
            for bin_idx, m_raw in enumerate(sorted_markets):
                cid = m_raw.get("conditionId", m_raw.get("id"))
                if cid:
                    market_id_set.add(cid)
                    token_to_bin[cid] = (idx, bin_idx)
        else:
            # Binary or fallback: condition_id
            cid = parsed.condition_id
            if cid:
                market_id_set.add(cid)

    # Step 3: for each day in the range, fetch order book snapshots
    loop = asyncio.get_event_loop()
    all_orderbooks: dict[str, dict] = {}

    unique_dates = sorted(list(set(
        p.resolution_date for p, _ in all_pairs if p.resolution_date
    )))
    
    log.info("Fetching pmxt archive order books for %d unique dates (latency=%ds)...", 
             len(unique_dates), latency_seconds)
    
    for d in tqdm(unique_dates, desc="pmxt Archive", unit="day", ncols=80):
        # Calculate precise decision timestamp based on model release
        # Default to 00Z run
        release_info = MODEL_RELEASE_CHART.get(model_name, {}).get("00")
        if release_info:
            h, m = release_info
            # We assume decision is made on the SAME day as resolution for daily weather,
            # using the 00Z model run that arrives shortly after.
            target_ts = datetime.combine(d, datetime.min.time()) + timedelta(
                hours=h, minutes=m, seconds=latency_seconds
            )
        else:
            # Fallback: midday on resolution date
            target_ts = datetime.combine(d, datetime.min.time()) + timedelta(hours=12)

        ob = await loop.run_in_executor(
            None, fetch_orderbook_at_timestamp, target_ts, market_id_set,
        )
        # Keep the latest snapshot we find for each market
        for mid, data in ob.items():
            all_orderbooks[mid] = data

    log.info("Collected order book data for %d / %d market IDs from pmxt archive",
             len(all_orderbooks), len(market_id_set))

    # Step 4: enrich ParsedMarket prices with real bid/ask
    enriched_pairs: list[tuple[ParsedMarket, dict]] = []
    enriched_count = 0

    for idx, (parsed, raw) in enumerate(all_pairs):
        if "markets" in raw and len(raw["markets"]) > 0:
            # Multi-outcome: build per-bin orderbook dict using condition_id
            sorted_markets = sorted(raw["markets"], key=lambda m: int(m.get("groupItemThreshold", "0")))
            per_bin_obs: dict[int, dict] = {}
            pmxt_obs: dict[str, dict] = {}
            
            for bin_idx, m_raw in enumerate(sorted_markets):
                cid = m_raw.get("conditionId", m_raw.get("id"))
                if cid and cid in all_orderbooks:
                    per_bin_obs[bin_idx] = all_orderbooks[cid]
                    # Map the found orderbook to the correct token_id for the pipeline
                    if bin_idx < len(parsed.token_ids):
                        pmxt_obs[parsed.token_ids[bin_idx]] = all_orderbooks[cid]
            
            if per_bin_obs:
                parsed = enrich_market_with_orderbook(
                    parsed, per_bin_orderbooks=per_bin_obs
                )
                enriched_count += 1
                raw["_pmxt_orderbook"] = pmxt_obs
        else:
            # Binary market: lookup using condition_id 
            cid = parsed.condition_id
            ob = all_orderbooks.get(cid)

            if ob:
                parsed = enrich_market_with_orderbook(parsed, orderbook=ob)
                enriched_count += 1
                raw["_pmxt_orderbook"] = ob
            else:
                log.warning("No orderbook found for binary market %s on date. Prices will be 0.0", cid)

        enriched_pairs.append((parsed, raw))

    log.info("Enriched %d markets with pmxt historical prices (%d unchanged)",
             enriched_count, len(all_pairs) - enriched_count)

    return enriched_pairs
