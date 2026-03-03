"""
Open-Meteo forecast puller — upgraded with:
1. ECMWF ensemble endpoint (51 members) for real probabilistic uncertainty
2. Multi-model deterministic forecasts as secondary source
3. Atmospheric regime variables (CAPE, 850hPa wind) for regime flags
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx
import numpy as np

from weatheredge.config import FORECAST_DAYS, OPEN_METEO_BASE, OPEN_METEO_MODELS
from weatheredge.models import ForecastResult

OPEN_METEO_ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"


# =========================================================================
# Atmospheric regime data
# =========================================================================

@dataclass
class AtmosphericRegime:
    """Atmospheric regime variables for a station on a given day."""

    station_id: str
    valid_date: date
    cape_max: float | None = None          # J/kg — convective available potential energy
    wind_dir_850hpa: float | None = None   # degrees — steering level wind direction
    wind_speed_850hpa: float | None = None # km/h
    ensemble_spread: float = 0.0           # °C — 90th-10th percentile from ensemble
    model_divergence: float = 0.0          # °C — abs diff between best two models


# =========================================================================
# Public API
# =========================================================================

async def fetch_forecast(
    latitude: float,
    longitude: float,
    timezone: str = "auto",
    models: list[str] | None = None,
    forecast_days: int = FORECAST_DAYS,
) -> list[ForecastResult]:
    """
    Fetch daily temperature forecasts using the ensemble endpoint first,
    falling back to multi-model deterministic if ensemble fails.

    Returns one ForecastResult per forecast day with proper probabilistic uncertainty.
    """
    # Try ensemble first (51 members = real uncertainty)
    ensemble_results = await _fetch_ensemble(latitude, longitude, timezone, forecast_days)
    if ensemble_results:
        return ensemble_results

    # Fallback: multi-model deterministic
    models = models or OPEN_METEO_MODELS
    raw_forecasts = await _fetch_raw_multimodel(latitude, longitude, timezone, models, forecast_days)
    return _build_multimodel_ensemble(raw_forecasts, models)


async def fetch_regime_data(
    latitude: float,
    longitude: float,
    timezone: str = "auto",
    forecast_days: int = FORECAST_DAYS,
) -> list[AtmosphericRegime]:
    """
    Fetch atmospheric regime variables: CAPE, 850hPa winds.
    These are used by the meteorologist regime flags.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "cape,windspeed_850hPa,winddirection_850hPa",
        "timezone": timezone,
        "forecast_days": forecast_days,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(OPEN_METEO_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    cape_vals = hourly.get("cape", [])
    ws_850 = hourly.get("windspeed_850hPa", [])
    wd_850 = hourly.get("winddirection_850hPa", [])

    # Aggregate hourly to daily
    from collections import defaultdict
    daily: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"cape": [], "ws": [], "wd": []}
    )

    for i, t in enumerate(times):
        day = t[:10]  # "YYYY-MM-DD"
        if i < len(cape_vals) and cape_vals[i] is not None:
            daily[day]["cape"].append(cape_vals[i])
        if i < len(ws_850) and ws_850[i] is not None:
            daily[day]["ws"].append(ws_850[i])
        if i < len(wd_850) and wd_850[i] is not None:
            daily[day]["wd"].append(wd_850[i])

    results: list[AtmosphericRegime] = []
    for day_str in sorted(daily.keys()):
        d = daily[day_str]
        results.append(AtmosphericRegime(
            station_id="",  # filled by caller
            valid_date=date.fromisoformat(day_str),
            cape_max=max(d["cape"]) if d["cape"] else None,
            wind_dir_850hpa=float(np.mean(d["wd"])) if d["wd"] else None,
            wind_speed_850hpa=float(np.mean(d["ws"])) if d["ws"] else None,
        ))

    return results


# =========================================================================
# Ensemble endpoint (51 ECMWF members)
# =========================================================================

async def _fetch_ensemble(
    lat: float,
    lon: float,
    timezone: str,
    forecast_days: int,
) -> list[ForecastResult]:
    """
    Fetch ECMWF ensemble (51 members) from Open-Meteo ensemble API.
    Gives real probabilistic uncertainty instead of multi-model hack.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": timezone,
        "forecast_days": forecast_days,
        "models": "ecmwf_ifs",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(OPEN_METEO_ENSEMBLE, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    daily = data.get("daily", {})
    dates = daily.get("time", [])

    if not dates:
        return []

    # Ensemble API returns temperature_2m_max_member0 .. member50
    tmax_members: list[list[float]] = []
    tmin_members: list[list[float]] = []

    for key, values in daily.items():
        if key.startswith("temperature_2m_max_member"):
            tmax_members.append([v if v is not None else float("nan") for v in values])
        elif key.startswith("temperature_2m_min_member"):
            tmin_members.append([v if v is not None else float("nan") for v in values])

    if not tmax_members:
        # Try non-ensemble format (single series)
        tmax_raw = daily.get("temperature_2m_max", [])
        tmin_raw = daily.get("temperature_2m_min", [])
        if tmax_raw:
            return [
                ForecastResult(
                    station_id="",
                    valid_date=date.fromisoformat(d),
                    tmax_mean=float(tmax_raw[i]) if tmax_raw[i] is not None else 0,
                    tmax_std=2.0,  # default
                    tmin_mean=float(tmin_raw[i]) if i < len(tmin_raw) and tmin_raw[i] is not None else None,
                    tmin_std=2.0,
                    models_used=["ecmwf_ifs_ensemble"],
                )
                for i, d in enumerate(dates) if i < len(tmax_raw)
            ]
        return []

    results: list[ForecastResult] = []
    n_days = len(dates)

    for i in range(n_days):
        tmax_vals = [m[i] for m in tmax_members if i < len(m) and not np.isnan(m[i])]
        tmin_vals = [m[i] for m in tmin_members if i < len(m) and not np.isnan(m[i])]

        if not tmax_vals:
            continue

        tmax_arr = np.array(tmax_vals)
        tmin_arr = np.array(tmin_vals) if tmin_vals else None

        # Compute spread (90th - 10th percentile) for regime flagging
        spread = float(np.percentile(tmax_arr, 90) - np.percentile(tmax_arr, 10))

        results.append(ForecastResult(
            station_id="",
            valid_date=date.fromisoformat(dates[i]),
            tmax_mean=float(np.mean(tmax_arr)),
            tmax_std=float(np.std(tmax_arr)),
            tmin_mean=float(np.mean(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 0 else None,
            tmin_std=float(np.std(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 1 else 2.0,
            models_used=["ecmwf_ifs_ensemble"],
        ))

    return results


# =========================================================================
# Multi-model deterministic (fallback)
# =========================================================================

async def _fetch_raw_multimodel(
    lat: float,
    lon: float,
    timezone: str,
    models: list[str],
    forecast_days: int,
) -> dict[str, dict[str, list[float]]]:
    """Fetch deterministic forecasts from multiple models."""
    results: dict[str, dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=30) as client:
        for model in models:
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "models": model,
                "timezone": timezone,
                "forecast_days": forecast_days,
            }

            try:
                resp = await client.get(OPEN_METEO_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue

            daily = data.get("daily", {})
            dates = daily.get("time", [])
            tmax = daily.get("temperature_2m_max", [])
            tmin = daily.get("temperature_2m_min", [])

            if dates and tmax:
                results[model] = {
                    "dates": dates,
                    "tmax": [v if v is not None else float("nan") for v in tmax],
                    "tmin": [v if v is not None else float("nan") for v in tmin],
                }

    return results


def _build_multimodel_ensemble(
    raw: dict[str, dict[str, Any]],
    models: list[str],
) -> list[ForecastResult]:
    """Compute mean/std across deterministic models (fallback)."""
    if not raw:
        return []

    first_model = next(iter(raw.values()))
    dates = first_model["dates"]
    results: list[ForecastResult] = []
    DEFAULT_STD = 2.0

    for i, date_str in enumerate(dates):
        tmax_values: list[float] = []
        tmin_values: list[float] = []
        used_models: list[str] = []

        for model in models:
            if model not in raw:
                continue
            model_data = raw[model]
            if i < len(model_data["tmax"]):
                val = model_data["tmax"][i]
                if not np.isnan(val):
                    tmax_values.append(val)
                    if model not in used_models:
                        used_models.append(model)
            if i < len(model_data["tmin"]):
                val = model_data["tmin"][i]
                if not np.isnan(val):
                    tmin_values.append(val)

        if not tmax_values:
            continue

        tmax_arr = np.array(tmax_values)
        tmin_arr = np.array(tmin_values) if tmin_values else None

        results.append(
            ForecastResult(
                station_id="",
                valid_date=date.fromisoformat(date_str),
                tmax_mean=float(np.mean(tmax_arr)),
                tmax_std=float(np.std(tmax_arr)) if len(tmax_arr) > 1 else DEFAULT_STD,
                tmin_mean=float(np.mean(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 0 else None,
                tmin_std=float(np.std(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 1 else DEFAULT_STD,
                models_used=used_models,
            )
        )

    return results
