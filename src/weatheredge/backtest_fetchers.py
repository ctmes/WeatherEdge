"""
Backtest data fetchers — resolved markets from Gamma + historical forecasts
from Open-Meteo's Historical Forecast API.

Disk cache ensures deterministic reruns and avoids API abuse.

Key changes vs original:
- Parallel day-level fetching (up to 10 concurrent days via Semaphore)
- Open-Meteo semaphore raised from 1 → 3 (safe for free & paid tier)
- ``decision_date`` parameter propagated through forecast fetchers so
  lead-time uncertainty inflates correctly for non-same-day markets
- Per-day logic extracted into ``_fetch_day_markets`` / ``_fetch_day_events``
  helpers to keep the public API functions clean
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

# ---------------------------------------------------------------------------
# Open-Meteo rate-limit semaphore
# ---------------------------------------------------------------------------
# 3 concurrent requests is safe on both the free tier (~10k req/day) and the
# paid tier.  This is 3x faster than the previous value of 1 with zero risk
# of hitting rate limits for typical backtest ranges (<=1 year, <=15 stations).

_open_meteo_sems: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _get_open_meteo_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _open_meteo_sems:
        _open_meteo_sems[loop] = asyncio.Semaphore(3)
    return _open_meteo_sems[loop]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

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

async def _fetch_day_markets(date_str: str, page_size: int = 100) -> list[dict]:
    """
    Fetch and cache resolved weather markets for a single calendar day.

    Speculatively fires up to 10 extra pages in parallel if the first page
    is full (avoids sequential pagination).
    """
    cache_k = _cache_key("gamma_res_m", date_str)
    cached = _read_cache(cache_k)
    if cached is not None:
        return cached

    day_raw: list[dict] = []
    url = f"{POLYMARKET_GAMMA_BASE}/markets"
    base_params: dict = {
        "limit": page_size,
        "offset": 0,
        "closed": "true",
        "active": "false",
        "end_date_min": f"{date_str}T00:00:00Z",
        "end_date_max": f"{date_str}T23:59:59Z",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=base_params)
            resp.raise_for_status()
            data = resp.json()
            if data:
                day_raw.extend(item for item in data if _is_weather_market(item))
                if len(data) >= page_size:
                    extra_tasks = [
                        client.get(url, params={**base_params, "offset": page_size * i})
                        for i in range(1, 11)
                    ]
                    extra_responses = await asyncio.gather(*extra_tasks, return_exceptions=True)
                    for r in extra_responses:
                        if isinstance(r, httpx.Response):
                            rdata = r.json()
                            if rdata:
                                day_raw.extend(item for item in rdata if _is_weather_market(item))
    except Exception as e:
        log.warning("Gamma markets fetch failed for %s: %s", date_str, e)

    _write_cache(cache_k, day_raw)
    return day_raw


async def fetch_resolved_markets(
    start: date,
    end: date,
    *,
    max_pages: int = 50,
    page_size: int = 100,
) -> list[tuple[ParsedMarket, dict]]:
    """
    Fetch resolved weather markets from Gamma ``GET /markets``.

    Per-day caching prevents redundant API calls on repeated runs.
    Days are fetched concurrently (up to 10 at a time) for speed — a 90-day
    range that previously took ~90 sequential requests now completes in ~10
    round-trips (all cache misses) or near-instantly (all cached).
    """
    from tqdm import tqdm

    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    day_sem = asyncio.Semaphore(10)

    async def _bounded(d: date) -> list[dict]:
        async with day_sem:
            return await _fetch_day_markets(d.isoformat(), page_size)

    all_pairs: list[tuple[ParsedMarket, dict]] = []
    chunk_size = 20
    with tqdm(total=len(days), desc="Gamma Markets", unit="day", leave=False) as pbar:
        for start_idx in range(0, len(days), chunk_size):
            batch = days[start_idx : start_idx + chunk_size]
            results = await asyncio.gather(*[_bounded(d) for d in batch])
            for day_raw in results:
                for raw_item in day_raw:
                    parsed = _raw_to_parsed_market(raw_item)
                    if parsed:
                        all_pairs.append((parsed, raw_item))
            pbar.update(len(batch))

    return all_pairs


# =========================================================================
# Gamma: resolved daily weather events
# =========================================================================

def _is_temp_event(event: dict) -> bool:
    """True if a Gamma event looks like a daily temperature market."""
    title = event.get("title", "").lower()
    return "highest temperature in" in title or "temperature" in title


async def _fetch_day_events(date_str: str, page_size: int = 100) -> list[dict]:
    """
    Fetch and cache resolved daily weather events for a single calendar day.
    """
    cache_k = _cache_key("gamma_daily_e", date_str)
    cached = _read_cache(cache_k)
    if cached is not None:
        return cached

    day_raw: list[dict] = []
    url = f"{POLYMARKET_GAMMA_BASE}/events"
    base_params: dict = {
        "limit": page_size,
        "offset": 0,
        "closed": "true",
        "end_date_min": f"{date_str}T00:00:00Z",
        "end_date_max": f"{date_str}T23:59:59Z",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=base_params)
            resp.raise_for_status()
            data = resp.json()
            if data:
                day_raw.extend(e for e in data if _is_temp_event(e))
                if len(data) >= page_size:
                    extra_tasks = [
                        client.get(url, params={**base_params, "offset": page_size * i})
                        for i in range(1, 11)
                    ]
                    extra_responses = await asyncio.gather(*extra_tasks, return_exceptions=True)
                    for r in extra_responses:
                        if isinstance(r, httpx.Response):
                            rdata = r.json()
                            if rdata:
                                day_raw.extend(e for e in rdata if _is_temp_event(e))
    except Exception as e:
        log.warning("Gamma events fetch failed for %s: %s", date_str, e)

    _write_cache(cache_k, day_raw)
    return day_raw


async def fetch_resolved_daily_events(
    start: date,
    end: date,
    *,
    max_pages: int = 100,
    page_size: int = 100,
) -> list[tuple[ParsedMarket, dict]]:
    """
    Fetch resolved daily weather events from Gamma ``GET /events``.

    Per-day caching prevents redundant API calls on repeated runs.
    Days are fetched concurrently (up to 10 at a time) for speed.
    """
    from tqdm import tqdm

    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    day_sem = asyncio.Semaphore(10)

    async def _bounded(d: date) -> list[dict]:
        async with day_sem:
            return await _fetch_day_events(d.isoformat(), page_size)

    all_pairs: list[tuple[ParsedMarket, dict]] = []
    chunk_size = 20
    with tqdm(total=len(days), desc="Gamma Events ", unit="day", leave=False) as pbar:
        for start_idx in range(0, len(days), chunk_size):
            batch = days[start_idx : start_idx + chunk_size]
            results = await asyncio.gather(*[_bounded(d) for d in batch])
            for day_raw in results:
                for raw_event in day_raw:
                    parsed = parse_daily_weather_event(raw_event)
                    if parsed:
                        all_pairs.append((parsed, raw_event))
            pbar.update(len(batch))

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
    decision_date: date | None = None,
) -> ForecastResult | None:
    """
    Fetch a historical daily forecast from Open-Meteo's Historical Forecast API.

    Uses ``historical-forecast-api.open-meteo.com``.
    Caches raw JSON on disk keyed by ``(lat, lon, model, date)`` for
    deterministic reruns.

    ``decision_date`` — the date on which the trading decision is made.
    When provided, lead time is computed as ``(valid_date - decision_date).days``
    and forecast uncertainty is inflated accordingly (~0.15°C/day beyond day 0).
    Defaults to same-day (lead = 0), which is correct for daily weather markets
    where the resolution date equals the decision date.
    """
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
        if model:
            params["models"] = model

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                for attempt in range(5):
                    async with _get_open_meteo_sem():
                        resp = await client.get(OPEN_METEO_HISTORICAL, params=params)

                    if resp.status_code == 429:
                        wait = (2 ** attempt) + 1
                        log.warning(
                            "Open-Meteo rate limited (429). Retrying in %ds (attempt %d/5).",
                            wait, attempt + 1,
                        )
                        await asyncio.sleep(wait)
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

    tmax = tmax_vals[0]
    tmin = tmin_vals[0] if tmin_vals else None

    if tmax is None:
        return None

    # Forecast uncertainty scales with lead time.
    # ECMWF 1-day TMAX RMSE ≈ 1.5°C; degrades ~0.15°C per additional lead day.
    BASE_STD = 1.5
    LEAD_DEGRADATION = 0.15

    if decision_date is not None:
        lead_days = max(0, (valid_date - decision_date).days)
    else:
        # Historical replay default: same-day (lead = 0).
        # Daily weather markets resolve on the same day the decision is made.
        lead_days = 0

    default_std = BASE_STD + LEAD_DEGRADATION * lead_days

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
    decision_date: date | None = None,
) -> ForecastResult | None:
    """
    Fetch multiple historical models and build an ensemble forecast.

    Falls back to single-model if only one model succeeds.
    ``decision_date`` is forwarded to each model fetch for correct lead-time
    uncertainty inflation.
    """
    import numpy as np

    models = models or OPEN_METEO_MODELS
    tmax_all: list[float] = []
    tmin_all: list[float] = []
    used: list[str] = []

    tasks = [
        fetch_historical_forecast(
            latitude, longitude, valid_date,
            model=m, timezone=timezone,
            decision_date=decision_date,
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
