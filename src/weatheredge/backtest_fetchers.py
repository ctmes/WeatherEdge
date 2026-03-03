"""
Backtest data fetchers — resolved markets from Gamma + historical forecasts
from Open-Meteo's Historical Forecast API.

Disk cache ensures deterministic reruns and avoids API abuse.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from weatheredge.config import (
    OPEN_METEO_HISTORICAL,
    OPEN_METEO_MODELS,
    POLYMARKET_GAMMA_BASE,
)
from weatheredge.models import ForecastResult, ParsedMarket
from weatheredge.polymarket import _is_weather_market, _raw_to_parsed_market, parse_daily_weather_event

log = logging.getLogger("weatheredge.backtest_fetchers")

# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

_CACHE_DIR = Path.home() / ".weatheredge" / "backtest_cache"


def _cache_key(*parts: str) -> str:
    """Deterministic filename from key components."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16] + ".json"


def _read_cache(key: str) -> dict | None:
    path = _CACHE_DIR / key
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _write_cache(key: str, data: Any) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / key
    path.write_text(json.dumps(data, default=str), encoding="utf-8")


def cache_fingerprint() -> str:
    """Hash of all cached files — identifies the exact data snapshot."""
    if not _CACHE_DIR.exists():
        return "empty"
    files = sorted(_CACHE_DIR.glob("*.json"))
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


# =========================================================================
# Gamma: resolved markets
# =========================================================================

async def fetch_resolved_markets(
    start: date,
    end: date,
    *,
    max_pages: int = 50,
    page_size: int = 100,
) -> list[tuple[ParsedMarket, dict]]:
    """
    Fetch resolved weather markets from Gamma ``GET /markets``.

    Returns ``(ParsedMarket, raw_dict)`` pairs.  The raw dict is kept so
    the caller can extract ``resolved_outcome`` and other metadata that
    doesn't survive the ParsedMarket conversion.
    """
    cache_k = _cache_key("gamma_resolved", start.isoformat(), end.isoformat())
    cached = _read_cache(cache_k)
    if cached is not None:
        log.info("Loaded %d resolved markets from cache", len(cached))
        result: list[tuple[ParsedMarket, dict]] = []
        for raw_item in cached:
            parsed = _raw_to_parsed_market(raw_item)
            if parsed is not None:
                result.append((parsed, raw_item))
        return result

    all_raw: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(max_pages):
            url = f"{POLYMARKET_GAMMA_BASE}/markets"
            params = {
                "limit": page_size,
                "offset": offset,
                "closed": "true",
                "active": "false",
                "end_date_min": start.isoformat() + "T00:00:00Z",
                "end_date_max": end.isoformat() + "T23:59:59Z",
            }
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warning("Gamma fetch failed (offset=%d): %s", offset, e)
                break

            if not data:
                break

            for item in data:
                if _is_weather_market(item):
                    # Filter by resolution date within our window
                    end_date_str = item.get("endDate", "")
                    if end_date_str:
                        try:
                            market_end = datetime.fromisoformat(
                                end_date_str.replace("Z", "+00:00")
                            ).date()
                            if start <= market_end <= end:
                                all_raw.append(item)
                        except (ValueError, TypeError):
                            pass

            if len(data) < page_size:
                break
            offset += page_size

    # Cache the full raw response for deterministic reruns
    _write_cache(cache_k, all_raw)
    log.info("Fetched %d resolved weather markets from Gamma", len(all_raw))

    result = []
    for raw_item in all_raw:
        parsed = _raw_to_parsed_market(raw_item)
        if parsed is not None:
            result.append((parsed, raw_item))
    return result


# =========================================================================
# Gamma: resolved daily weather events
# =========================================================================

async def fetch_resolved_daily_events(
    start: date,
    end: date,
    *,
    max_pages: int = 100,
    page_size: int = 100,
) -> list[tuple[ParsedMarket, dict]]:
    """
    Fetch resolved daily weather events from Gamma ``GET /events``.

    Daily weather markets are structured as events with child markets
    (one per temperature bin). This function fetches the parent events,
    filters for daily weather markets, and parses them into ParsedMarket
    objects with complete bin sets.

    Returns ``(ParsedMarket, raw_event_dict)`` pairs.
    """
    cache_k = _cache_key("gamma_daily_events", start.isoformat(), end.isoformat())
    cached = _read_cache(cache_k)
    if cached is not None:
        log.info("Loaded %d daily weather events from cache", len(cached))
        result: list[tuple[ParsedMarket, dict]] = []
        for raw_event in cached:
            parsed = parse_daily_weather_event(raw_event)
            if parsed is not None:
                result.append((parsed, raw_event))
        return result

    all_raw: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(max_pages):
            url = f"{POLYMARKET_GAMMA_BASE}/events"
            params = {
                "limit": page_size,
                "offset": offset,
                "closed": "true",
            }
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warning("Gamma event fetch failed (offset=%d): %s", offset, e)
                break

            if not data:
                break

            for event in data:
                title = event.get("title", "").lower()
                if "highest temperature in" not in title:
                    continue

                # Filter by end date within our window
                end_date_str = event.get("endDate", "")
                if end_date_str:
                    try:
                        event_end = datetime.fromisoformat(
                            end_date_str.replace("Z", "+00:00")
                        ).date()
                        if start <= event_end <= end:
                            all_raw.append(event)
                    except (ValueError, TypeError):
                        pass

            if len(data) < page_size:
                break
            offset += page_size

    # Cache for deterministic reruns
    _write_cache(cache_k, all_raw)
    log.info("Fetched %d resolved daily weather events from Gamma", len(all_raw))

    result = []
    for raw_event in all_raw:
        parsed = parse_daily_weather_event(raw_event)
        if parsed is not None:
            result.append((parsed, raw_event))
    return result


def extract_winner_from_event(raw_event: dict) -> int | None:
    """
    Determine which bin index won from a resolved daily weather event.

    Scans child markets sorted by groupItemThreshold, returns the index
    of the child whose Yes outcome price ≈ 1.0.
    """
    markets = raw_event.get("markets", [])
    if not markets:
        return None

    sorted_markets = sorted(
        markets,
        key=lambda m: int(m.get("groupItemThreshold", "0")),
    )

    for i, m in enumerate(sorted_markets):
        prices_raw = m.get("outcomePrices", "")
        if isinstance(prices_raw, str) and prices_raw.strip().startswith("["):
            try:
                prices_list = json.loads(prices_raw)
                yes_price = float(prices_list[0])
            except (json.JSONDecodeError, IndexError, ValueError):
                continue
        elif isinstance(prices_raw, list):
            yes_price = float(prices_raw[0]) if prices_raw else 0.0
        else:
            continue

        if yes_price >= 0.8:
            return i

    return None


def extract_resolved_outcome(raw: dict) -> int | None:
    """
    Determine which bin index won from a resolved Gamma market.

    Gamma stores ``outcomePrices`` — after resolution, the winning outcome
    has price ≈ 1.0 and losers ≈ 0.0.  We pick the index of the max price.
    Returns ``None`` if no clear winner found.
    """
    import json
    prices_raw = raw.get("outcomePrices", "")
    if isinstance(prices_raw, str):
        if prices_raw.strip().startswith("["):
            try:
                prices_raw = json.loads(prices_raw)
                prices = [float(p) for p in prices_raw]
            except json.JSONDecodeError:
                prices = [float(p.strip()) for p in prices_raw.split(",") if p.strip()]
        else:
            prices = [float(p.strip()) for p in prices_raw.split(",") if p.strip()]
    elif isinstance(prices_raw, list):
        prices = [float(p) for p in prices_raw]
    else:
        return None

    if not prices:
        return None

    max_price = max(prices)
    # After resolution the winning outcome should be ≈1.0
    if max_price < 0.8:
        return None  # not clearly resolved

    return prices.index(max_price)


# =========================================================================
# Open-Meteo: historical forecasts
# =========================================================================

async def fetch_historical_forecast(
    latitude: float,
    longitude: float,
    valid_date: date,
    *,
    model: str = "ecmwf_ifs",
    timezone: str = "auto",
) -> ForecastResult | None:
    """
    Fetch a historical daily forecast from Open-Meteo's Historical Forecast API.

    Uses ``historical-forecast-api.open-meteo.com`` (correct host per user research).
    Caches raw JSON on disk keyed by ``(lat, lon, model, date)`` for deterministic
    reruns.
    """
    import numpy as np

    # Round coords to avoid cache misses from floating-point jitter
    lat_r = round(latitude, 4)
    lon_r = round(longitude, 4)

    cache_k = _cache_key("fc", str(lat_r), str(lon_r), model, valid_date.isoformat())
    cached = _read_cache(cache_k)

    if cached is None:
        params = {
            "latitude": lat_r,
            "longitude": lon_r,
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": timezone,
            "start_date": valid_date.isoformat(),
            "end_date": valid_date.isoformat(),
        }
        # Only add models param for non-default models
        if model:
            params["models"] = model

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                max_retries = 5
                for attempt in range(max_retries):
                    resp = await client.get(OPEN_METEO_HISTORICAL, params=params)
                    
                    if resp.status_code == 429:
                        wait_time = (2 ** attempt) + 1
                        log.warning("Rate limited (429). Retrying in %ds... (attempt %d/%d)", 
                                    wait_time, attempt + 1, max_retries)
                        import asyncio
                        await asyncio.sleep(wait_time)
                        continue
                        
                    resp.raise_for_status()
                    cached = resp.json()
                    _write_cache(cache_k, cached)
                    break
        except Exception as e:
            log.warning(
                "Historical forecast fetch failed (%s %s %s): %s",
                lat_r, lon_r, valid_date, e,
            )
            return None

    daily = cached.get("daily", {})
    dates = daily.get("time", [])
    tmax_vals = daily.get("temperature_2m_max", [])
    tmin_vals = daily.get("temperature_2m_min", [])

    if not dates or not tmax_vals:
        return None

    # Take the first (and usually only) day
    tmax = tmax_vals[0]
    tmin = tmin_vals[0] if tmin_vals else None

    if tmax is None:
        return None

    # For a single deterministic value, use a default std
    # (ensemble spread isn't available from the historical API for a single model)
    DEFAULT_STD = 2.0

    return ForecastResult(
        station_id="",  # filled by caller
        valid_date=valid_date,
        tmax_mean=float(tmax),
        tmax_std=DEFAULT_STD,
        tmin_mean=float(tmin) if tmin is not None else None,
        tmin_std=DEFAULT_STD,
        models_used=[model],
    )


async def fetch_historical_forecast_ensemble(
    latitude: float,
    longitude: float,
    valid_date: date,
    *,
    models: list[str] | None = None,
    timezone: str = "auto",
) -> ForecastResult | None:
    """
    Fetch multiple historical models and build an ensemble forecast.

    Falls back to single-model if only one succeeds.
    """
    import numpy as np

    models = models or OPEN_METEO_MODELS
    tmax_all: list[float] = []
    tmin_all: list[float] = []
    used: list[str] = []

    for m in models:
        fc = await fetch_historical_forecast(
            latitude, longitude, valid_date, model=m, timezone=timezone,
        )
        if fc is not None:
            tmax_all.append(fc.tmax_mean)
            if fc.tmin_mean is not None:
                tmin_all.append(fc.tmin_mean)
            used.append(m)

    if not tmax_all:
        return None

    tmax_arr = np.array(tmax_all)
    tmin_arr = np.array(tmin_all) if tmin_all else None

    return ForecastResult(
        station_id="",
        valid_date=valid_date,
        tmax_mean=float(np.mean(tmax_arr)),
        tmax_std=float(np.std(tmax_arr)) if len(tmax_arr) > 1 else 2.0,
        tmin_mean=float(np.mean(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 0 else None,
        tmin_std=float(np.std(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 1 else 2.0,
        models_used=used,
    )
