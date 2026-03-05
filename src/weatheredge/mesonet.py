"""
Iowa Environmental Mesonet (IEM) ASOS data puller — fetch historical
daily temperature observations for calibration and verification.

IEM is free, no API key required. Data source:
https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import numpy as np

from weatheredge.cache import cache_key as _cache_key, read_cache, write_cache

log = logging.getLogger("weatheredge.mesonet")

IEM_BASE = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
_SUBDIR = "mesonet_cache"


def _read_cache(key: str) -> list[dict[str, str]] | None:
    return read_cache(_SUBDIR, key)


def _write_cache(key: str, data: Any) -> None:
    write_cache(_SUBDIR, key, data)


# =========================================================================
# Data structures
# =========================================================================

@dataclass
class DailyObservation:
    """One day of observed weather at a station."""

    station_id: str
    date: date
    tmax_c: float | None = None       # daily max temp °C
    tmin_c: float | None = None       # daily min temp °C
    precip_mm: float | None = None    # daily precipitation mm


@dataclass
class HourlyObservation:
    """One hourly observation at a station."""

    station_id: str
    timestamp: datetime
    temp_c: float | None = None
    dwpt_c: float | None = None
    relh: float | None = None
    wind_dir: float | None = None      # degrees
    wind_speed_kt: float | None = None
    precip_mm: float | None = None


# =========================================================================
# Public API
# =========================================================================

async def fetch_daily_observations(
    station_id: str,
    start_date: date,
    end_date: date,
) -> list[DailyObservation]:
    """
    Fetch daily max/min temperature and precipitation from IEM ASOS.

    station_id: 4-letter ICAO code (e.g. NZWN, KORD)
    Returns one DailyObservation per day in the range.
    """
    raw_rows = await _fetch_raw(
        station_id=station_id,
        start_date=start_date,
        end_date=end_date,
        data_vars=["tmpf", "dwpf", "relh", "drct", "sknt", "p01i"],
    )

    if not raw_rows:
        return []

    return _aggregate_to_daily(station_id, raw_rows)


async def fetch_hourly_observations(
    station_id: str,
    start_date: date,
    end_date: date,
) -> list[HourlyObservation]:
    """
    Fetch hourly observations from IEM ASOS.
    Useful for understanding intra-day patterns (sea breeze timing, etc.).
    """
    raw_rows = await _fetch_raw(
        station_id=station_id,
        start_date=start_date,
        end_date=end_date,
        data_vars=["tmpf", "dwpf", "relh", "drct", "sknt", "p01i"],
    )

    observations: list[HourlyObservation] = []
    for row in raw_rows:
        try:
            ts = datetime.strptime(row.get("valid", ""), "%Y-%m-%d %H:%M")
            observations.append(HourlyObservation(
                station_id=station_id,
                timestamp=ts,
                temp_c=_f_to_c(row.get("tmpf")),
                dwpt_c=_f_to_c(row.get("dwpf")),
                relh=_safe_float(row.get("relh")),
                wind_dir=_safe_float(row.get("drct")),
                wind_speed_kt=_safe_float(row.get("sknt")),
                precip_mm=_inches_to_mm(row.get("p01i")),
            ))
        except (ValueError, KeyError):
            continue

    return observations


# =========================================================================
# Internals
# =========================================================================

async def _fetch_raw(
    station_id: str,
    start_date: date,
    end_date: date,
    data_vars: list[str],
) -> list[dict[str, str]]:
    """Fetch raw CSV data from IEM ASOS request endpoint."""
    cache_k = _cache_key("iem", station_id, start_date.isoformat(), end_date.isoformat(), ",".join(data_vars))
    cached = _read_cache(cache_k)
    if cached is not None:
        log.info("Loaded %d obs from cache for %s (%s to %s)", len(cached), station_id, start_date, end_date)
        return cached

    params = {
        "station": station_id,
        "data": ",".join(data_vars),
        "year1": start_date.year,
        "month1": start_date.month,
        "day1": start_date.day,
        "year2": end_date.year,
        "month2": end_date.month,
        "day2": end_date.day,
        "tz": "UTC",
        "format": "comma",
        "latlon": "yes",
        "elev": "yes",
        "missing": "M",
        "trace": "T",
        "report_type": "3",  # METAR + Specials
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(IEM_BASE, params=params)
        resp.raise_for_status()

    text = resp.text

    # IEM returns comment lines starting with #
    lines = [l for l in text.strip().split("\n") if not l.startswith("#")]
    if len(lines) < 2:
        _write_cache(cache_k, [])
        return []

    reader = csv.DictReader(lines)
    result = list(reader)
    _write_cache(cache_k, result)
    return result


def _aggregate_to_daily(
    station_id: str,
    rows: list[dict[str, str]],
) -> list[DailyObservation]:
    """Aggregate hourly ASOS observations to daily max/min/precip."""
    from collections import defaultdict

    daily: dict[date, dict[str, list[float]]] = defaultdict(
        lambda: {"temps": [], "precip": []}
    )

    for row in rows:
        try:
            ts = datetime.strptime(row.get("valid", ""), "%Y-%m-%d %H:%M")
            day = ts.date()
        except ValueError:
            continue

        temp = _f_to_c(row.get("tmpf"))
        if temp is not None:
            daily[day]["temps"].append(temp)

        precip = _inches_to_mm(row.get("p01i"))
        if precip is not None:
            daily[day]["precip"].append(precip)

    observations: list[DailyObservation] = []
    for day in sorted(daily.keys()):
        data = daily[day]
        temps = data["temps"]
        precip_vals = data["precip"]

        observations.append(DailyObservation(
            station_id=station_id,
            date=day,
            tmax_c=max(temps) if temps else None,
            tmin_c=min(temps) if temps else None,
            precip_mm=sum(precip_vals) if precip_vals else None,
        ))

    return observations


def _f_to_c(val: str | None) -> float | None:
    """Convert Fahrenheit string to Celsius float."""
    if val is None or val.strip() in ("M", "T", ""):
        return None
    try:
        return round((float(val) - 32) * 5 / 9, 2)
    except ValueError:
        return None


def _inches_to_mm(val: str | None) -> float | None:
    """Convert inches string to millimetres float."""
    if val is None or val.strip() in ("M", "T", ""):
        return None
    try:
        return round(float(val) * 25.4, 2)
    except ValueError:
        return None


def _safe_float(val: str | None) -> float | None:
    """Parse a float, returning None for missing markers."""
    if val is None or val.strip() in ("M", "T", ""):
        return None
    try:
        return float(val)
    except ValueError:
        return None
