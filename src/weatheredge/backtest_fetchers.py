"""
Backtest data fetchers — resolved markets from Gamma + historical forecasts
from Open-Meteo's Historical Forecast API.

Disk cache ensures deterministic reruns and avoids API abuse.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from weatheredge.cache import cache_key as _cache_key, read_cache, write_cache, cache_fingerprint as _cache_fp
from weatheredge.config import (
    OPEN_METEO_API_KEY,
    OPEN_METEO_HISTORICAL,
    OPEN_METEO_MODELS,
    POLYMARKET_GAMMA_BASE,
)
from weatheredge.models import ForecastResult, ParsedMarket
from weatheredge.polymarket import _is_weather_market, _raw_to_parsed_market, parse_daily_weather_event

log = logging.getLogger("weatheredge.backtest_fetchers")

_SUBDIR = "backtest_cache"

# Global semaphore to prevent hammering Open-Meteo API with concurrent requests
# (especially during walk-forward calibration which fetches 365 days x N models)
_open_meteo_sems: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}

def _get_open_meteo_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _open_meteo_sems:
        _open_meteo_sems[loop] = asyncio.Semaphore(1)
    return _open_meteo_sems[loop]



def _read_cache(key: str) -> dict | None:
    return read_cache(_SUBDIR, key)


def _write_cache(key: str, data: Any) -> None:
    write_cache(_SUBDIR, key, data)


def cache_fingerprint() -> str:
    """Hash of all cached files — identifies the exact data snapshot."""
    return _cache_fp(_SUBDIR)


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
    Uses per-day caching to avoid redundant fetches for overlapping ranges.
    """
    from tqdm.asyncio import tqdm
    all_pairs: list[tuple[ParsedMarket, dict]] = []
    
    current = start
    pbar = tqdm(total=(end - start).days + 1, desc="Gamma Markets", unit="day", leave=False)
    
    while current <= end:
        date_str = current.isoformat()
        cache_k = _cache_key("gamma_res_m", date_str)
        cached = _read_cache(cache_k)
        
        if cached is not None:
            day_raw = cached
        else:
            day_raw = []
            async with httpx.AsyncClient(timeout=30) as client:
                # Fetch first page to see if we need more
                url = f"{POLYMARKET_GAMMA_BASE}/markets"
                params = {
                    "limit": page_size,
                    "offset": 0,
                    "closed": "true",
                    "active": "false",
                    "end_date_min": f"{date_str}T00:00:00Z",
                    "end_date_max": f"{date_str}T23:59:59Z",
                }
                
                try:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    if data:
                        for item in data:
                            if _is_weather_market(item):
                                day_raw.append(item)
                        
                        # If first page was full, speculatively fetch more pages in parallel
                        if len(data) >= page_size:
                            # Fetch up to 10 more pages (offsets 100, 200, ..., 1000)
                            offsets = [page_size * i for i in range(1, 11)]
                            tasks = []
                            for offset in offsets:
                                p = params.copy()
                                p["offset"] = offset
                                tasks.append(client.get(url, params=p))
                            
                            responses = await asyncio.gather(*tasks, return_exceptions=True)
                            for r in responses:
                                if isinstance(r, httpx.Response):
                                    r_data = r.json()
                                    if not r_data: continue
                                    for item in r_data:
                                        if _is_weather_market(item):
                                            day_raw.append(item)
                except Exception as e:
                    log.warning("Gamma fetch failed for %s: %s", date_str, e)
            
            _write_cache(cache_k, day_raw)
            
        for raw_item in day_raw:
            parsed = _raw_to_parsed_market(raw_item)
            if parsed:
                all_pairs.append((parsed, raw_item))
        
        current += timedelta(days=1)
        pbar.update(1)
        
    pbar.close()
    return all_pairs


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
    Uses per-day caching to avoid redundant fetches for overlapping ranges.
    """
    from tqdm.asyncio import tqdm
    all_pairs: list[tuple[ParsedMarket, dict]] = []
    
    current = start
    pbar = tqdm(total=(end - start).days + 1, desc="Gamma Events ", unit="day", leave=False)
    
    while current <= end:
        date_str = current.isoformat()
        cache_k = _cache_key("gamma_daily_e", date_str)
        cached = _read_cache(cache_k)
        
        if cached is not None:
            day_raw = cached
        else:
            day_raw = []
            async with httpx.AsyncClient(timeout=30) as client:
                url = f"{POLYMARKET_GAMMA_BASE}/events"
                params = {
                    "limit": page_size,
                    "offset": 0,
                    "closed": "true",
                    "end_date_min": f"{date_str}T00:00:00Z",
                    "end_date_max": f"{date_str}T23:59:59Z",
                }
                
                try:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    if data:
                        for event in data:
                            title = event.get("title", "").lower()
                            if "highest temperature in" in title or "temperature" in title:
                                day_raw.append(event)
                        
                        if len(data) >= page_size:
                            offsets = [page_size * i for i in range(1, 11)]
                            tasks = []
                            for offset in offsets:
                                p = params.copy()
                                p["offset"] = offset
                                tasks.append(client.get(url, params=p))
                                
                            responses = await asyncio.gather(*tasks, return_exceptions=True)
                            for r in responses:
                                if isinstance(r, httpx.Response):
                                    r_data = r.json()
                                    if not r_data: continue
                                    for event in r_data:
                                        title = event.get("title", "").lower()
                                        if "highest temperature in" in title or "temperature" in title:
                                            day_raw.append(event)
                except Exception as e:
                    log.warning("Gamma fetch failed for %s: %s", date_str, e)

            _write_cache(cache_k, day_raw)
            
        for raw_event in day_raw:
            parsed = parse_daily_weather_event(raw_event)
            if parsed:
                all_pairs.append((parsed, raw_event))
        
        current += timedelta(days=1)
        pbar.update(1)
        
    pbar.close()
    return all_pairs


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
        if OPEN_METEO_API_KEY:
            params["apikey"] = OPEN_METEO_API_KEY
        # Only add models param for non-default models
        if model:
            params["models"] = model

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                max_retries = 5
                for attempt in range(max_retries):
                    async with _get_open_meteo_sem():
                        resp = await client.get(OPEN_METEO_HISTORICAL, params=params)
                    
                    if resp.status_code == 429:
                        wait_time = (2 ** attempt) + 1
                        log.warning("Rate limited (429). Retrying in %ds... (attempt %d/%d)", 
                                    wait_time, attempt + 1, max_retries)
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

    if cached is None:
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

    # For a single deterministic value, estimate std based on typical forecast
    # skill.  ECMWF 1-day TMAX RMSE is ~1.0°C; skill degrades ~0.15°C/day.
    # Previous value of 2.0°C was far too wide — on Polymarket's 1°F (~0.55°C)
    # bins it flattens probabilities across 4+ bins, destroying any useful signal.
    # Previous value of 2.0°C was far too wide.
    # New value of 1.0°C was too narrow for some stations.
    BASE_STD = 1.5  # 1-day deterministic NWP RMSE (°C)
    LEAD_DEGRADATION = 0.15  # °C per additional day of lead time

    # Estimate lead time: difference between when we'd make the forecast and
    # the valid date. Conservative: assume 1-day lead for historical replays.
    lead_days = 1
    default_std = BASE_STD + LEAD_DEGRADATION * max(0, lead_days - 1)

    return ForecastResult(
        station_id="",  # filled by caller
        valid_date=valid_date,
        tmax_mean=float(tmax),
        tmax_std=default_std,
        tmin_mean=float(tmin) if tmin is not None else None,
        tmin_std=default_std,
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
    import asyncio

    models = models or OPEN_METEO_MODELS
    tmax_all: list[float] = []
    tmin_all: list[float] = []
    used: list[str] = []

    tasks = [
        fetch_historical_forecast(
            latitude, longitude, valid_date, model=m, timezone=timezone,
        )
        for m in models
    ]
    results = await asyncio.gather(*tasks)

    for m, fc in zip(models, results):
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
        tmax_std=float(max(1.0, np.std(tmax_arr))) if len(tmax_arr) > 1 else 1.5,
        tmin_mean=float(np.mean(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 0 else None,
        tmin_std=float(max(1.0, np.std(tmin_arr))) if tmin_arr is not None and len(tmin_arr) > 1 else 1.5,
        models_used=used,
    )
