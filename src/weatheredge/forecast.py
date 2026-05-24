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

from weatheredge.cache import cache_key as _cache_key, read_cache, write_cache
from weatheredge.config import FORECAST_DAYS, OPEN_METEO_BASE, OPEN_METEO_MODELS, OPEN_METEO_API_KEY
from weatheredge.models import ForecastResult

_SUBDIR = "forecast_cache"
_ECMWF_KEY = "ecmwf_ifs"
_GFS_KEY = "gfs_seamless"

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
    # 1. Try Ensemble(s)
    # Check if we should blend BOM for AU/NZ
    # (Rough bounding box for AU/NZ: -50 < lat < -10 and 110 < lon < 185)
    is_au_nz = -50 < latitude < -10 and 110 < longitude < 185
    
    ensemble_results = await _fetch_ensemble(latitude, longitude, timezone, forecast_days)
    
    if is_au_nz:
        bom_ensemble = await _fetch_ensemble(
            latitude, longitude, timezone, forecast_days, model="bom_access_global"
        )
        if bom_ensemble:


            # Blend them
            ensemble_results = _blend_ensembles(ensemble_results, bom_ensemble)

    if ensemble_results:
        return ensemble_results

    # Fallback: multi-model deterministic
    models = models or OPEN_METEO_MODELS
    raw_forecasts = await _fetch_raw_multimodel(latitude, longitude, timezone, models, forecast_days)
    return _build_multimodel_ensemble(raw_forecasts, models)


def _blend_ensembles(
    e1: list[ForecastResult],
    e2: list[ForecastResult],
) -> list[ForecastResult]:
    """
    Blend two ensemble ForecastResult lists by pooling their members.
    Assumes models_used identifies the members (51 for ECMWF, 18 for BOM).
    """
    results: list[ForecastResult] = []
    e2_by_date = {r.valid_date: r for r in e2}

    for r1 in e1:
        valid_date = r1.valid_date
        if valid_date not in e2_by_date:
            results.append(r1)
            continue

        r2 = e2_by_date[valid_date]

        # Use the recorded member counts from each ensemble result.
        n1 = r1.ensemble_member_count or 1
        n2 = r2.ensemble_member_count or 1

        def _blend(m1: float, s1: float, m2: float, s2: float, c1: int, c2: int):
            m_new = (m1 * c1 + m2 * c2) / (c1 + c2)
            # Pooled variance formula for combining two sample distributions.
            v1, v2 = s1**2, s2**2
            ss1 = (c1 - 1) * v1 + c1 * m1**2
            ss2 = (c2 - 1) * v2 + c2 * m2**2
            v_new = (ss1 + ss2 - (c1 + c2) * m_new**2) / (c1 + c2 - 1)
            return m_new, float(np.sqrt(max(v_new, 0.01)))

        tmax_m, tmax_s = _blend(r1.tmax_mean, r1.tmax_std, r2.tmax_mean, r2.tmax_std, n1, n2)

        tmin_m, tmin_s = None, None
        if r1.tmin_mean is not None and r2.tmin_mean is not None:
            tmin_m, tmin_s = _blend(r1.tmin_mean, r1.tmin_std or 2.0, r2.tmin_mean, r2.tmin_std or 2.0, n1, n2)

        # For a normal distribution, 90th–10th percentile ≈ 2.56σ.
        blended_spread = tmax_s * 2.56

        results.append(ForecastResult(
            station_id=r1.station_id,
            valid_date=valid_date,
            tmax_mean=tmax_m,
            tmax_std=tmax_s,
            tmin_mean=tmin_m,
            tmin_std=tmin_s,
            models_used=sorted(list(set(r1.models_used + r2.models_used))),
            ensemble_spread=blended_spread,
            ensemble_member_count=n1 + n2,
        ))

    return results



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
    lat_r = round(latitude, 4)
    lon_r = round(longitude, 4)
    ck = _cache_key("regime", str(lat_r), str(lon_r), str(forecast_days))
    cached = read_cache(_SUBDIR, ck, max_age_hours=1.0)
    if cached is not None:
        return [
            AtmosphericRegime(
                station_id=r.get("station_id", ""),
                valid_date=date.fromisoformat(r["valid_date"]),
                cape_max=r.get("cape_max"),
                wind_dir_850hpa=r.get("wind_dir_850hpa"),
                wind_speed_850hpa=r.get("wind_speed_850hpa"),
            )
            for r in cached
        ]

    params = {
        "latitude": lat_r,
        "longitude": lon_r,
        "hourly": "cape,windspeed_850hPa,winddirection_850hPa",
        "timezone": timezone,
        "forecast_days": forecast_days,
    }
    if OPEN_METEO_API_KEY:
        params["apikey"] = OPEN_METEO_API_KEY

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
        # Wind direction is a circular variable (0°/360° wrap-around).
        # Arithmetic mean fails near 0°/360° (e.g. 350° and 10° → 180° instead of 0°).
        if d["wd"]:
            wd_rad = np.radians(d["wd"])
            circ_mean_rad = np.arctan2(np.mean(np.sin(wd_rad)), np.mean(np.cos(wd_rad)))
            wind_dir = float(np.degrees(circ_mean_rad) % 360)
        else:
            wind_dir = None

        results.append(AtmosphericRegime(
            station_id="",  # filled by caller
            valid_date=date.fromisoformat(day_str),
            cape_max=max(d["cape"]) if d["cape"] else None,
            wind_dir_850hpa=wind_dir,
            wind_speed_850hpa=float(np.mean(d["ws"])) if d["ws"] else None,
        ))

    # Cache the results
    write_cache(_SUBDIR, ck, [
        {
            "station_id": r.station_id,
            "valid_date": r.valid_date.isoformat(),
            "cape_max": r.cape_max,
            "wind_dir_850hpa": r.wind_dir_850hpa,
            "wind_speed_850hpa": r.wind_speed_850hpa,
        }
        for r in results
    ])

    return results


# =========================================================================
# Ensemble endpoint (51 ECMWF members)
# =========================================================================

async def _fetch_ensemble(
    lat: float,
    lon: float,
    timezone: str,
    forecast_days: int,
    model: str = "ecmwf_ifs",
) -> list[ForecastResult]:
    """
    Fetch ensemble (51 ECMWF members or 18 BOM members) from Open-Meteo ensemble API.
    Gives real probabilistic uncertainty instead of multi-model hack.
    """
    lat_r = round(lat, 4)
    lon_r = round(lon, 4)
    ck = _cache_key("ensemble", str(lat_r), str(lon_r), model, str(forecast_days))
    cached = read_cache(_SUBDIR, ck, max_age_hours=1.0)
    if cached is not None:
        return [
            ForecastResult(
                station_id=r.get("station_id", ""),
                valid_date=date.fromisoformat(r["valid_date"]),
                tmax_mean=r["tmax_mean"],
                tmax_std=r["tmax_std"],
                tmin_mean=r.get("tmin_mean"),
                tmin_std=r.get("tmin_std"),
                models_used=r.get("models_used", []),
                ensemble_spread=r.get("ensemble_spread", 0.0),
                ensemble_member_count=r.get("ensemble_member_count", 1),
            )
            for r in cached
        ]

    params = {
        "latitude": lat_r,
        "longitude": lon_r,
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": timezone,
        "forecast_days": forecast_days,
        "models": model,
    }
    if OPEN_METEO_API_KEY:
        params["apikey"] = OPEN_METEO_API_KEY

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
                    tmax_std=2.0,
                    tmin_mean=float(tmin_raw[i]) if i < len(tmin_raw) and tmin_raw[i] is not None else None,
                    tmin_std=2.0,
                    models_used=[f"{model}_ensemble"],
                    ensemble_spread=2.0 * 2.56,
                    ensemble_member_count=1,
                )
                for i, d in enumerate(dates) if i < len(tmax_raw)
            ]
        return []

    member_count = len(tmax_members)
    results: list[ForecastResult] = []
    n_days = len(dates)

    for i in range(n_days):
        tmax_vals = [m[i] for m in tmax_members if i < len(m) and not np.isnan(m[i])]
        tmin_vals = [m[i] for m in tmin_members if i < len(m) and not np.isnan(m[i])]

        if not tmax_vals:
            continue

        tmax_arr = np.array(tmax_vals)
        tmin_arr = np.array(tmin_vals) if tmin_vals else None

        # 90th–10th percentile spread across members for regime flagging.
        spread = float(np.percentile(tmax_arr, 90) - np.percentile(tmax_arr, 10))

        results.append(ForecastResult(
            station_id="",
            valid_date=date.fromisoformat(dates[i]),
            tmax_mean=float(np.mean(tmax_arr)),
            tmax_std=float(np.std(tmax_arr)),
            tmin_mean=float(np.mean(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 0 else None,
            tmin_std=float(np.std(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 1 else 2.0,
            models_used=[f"{model}_ensemble"],
            ensemble_spread=spread,
            ensemble_member_count=member_count,
        ))

    # Cache results
    def _fc_to_dict(r: ForecastResult) -> dict:
        return {
            "station_id": r.station_id,
            "valid_date": r.valid_date.isoformat(),
            "tmax_mean": r.tmax_mean,
            "tmax_std": r.tmax_std,
            "tmin_mean": r.tmin_mean,
            "tmin_std": r.tmin_std,
            "models_used": r.models_used,
            "ensemble_spread": r.ensemble_spread,
            "ensemble_member_count": r.ensemble_member_count,
        }
    write_cache(_SUBDIR, ck, [_fc_to_dict(r) for r in results])

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
    lat_r = round(lat, 4)
    lon_r = round(lon, 4)
    ck = _cache_key("multimodel", str(lat_r), str(lon_r), ",".join(sorted(models)), str(forecast_days))
    cached = read_cache(_SUBDIR, ck, max_age_hours=1.0)
    if cached is not None:
        return cached

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
            if OPEN_METEO_API_KEY:
                params["apikey"] = OPEN_METEO_API_KEY

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

    if results:
        write_cache(_SUBDIR, ck, results)

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

        # Compute GFS/ECMWF divergence when both are present in the raw data.
        model_divergence = 0.0
        if _ECMWF_KEY in raw and _GFS_KEY in raw:
            ecmwf_tmax_list = raw[_ECMWF_KEY].get("tmax", [])
            gfs_tmax_list = raw[_GFS_KEY].get("tmax", [])
            if i < len(ecmwf_tmax_list) and i < len(gfs_tmax_list):
                ev, gv = ecmwf_tmax_list[i], gfs_tmax_list[i]
                if not (np.isnan(ev) or np.isnan(gv)):
                    model_divergence = abs(ev - gv)

        tmax_std = float(np.std(tmax_arr)) if len(tmax_arr) > 1 else DEFAULT_STD
        results.append(
            ForecastResult(
                station_id="",
                valid_date=date.fromisoformat(date_str),
                tmax_mean=float(np.mean(tmax_arr)),
                tmax_std=tmax_std,
                tmin_mean=float(np.mean(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 0 else None,
                tmin_std=float(np.std(tmin_arr)) if tmin_arr is not None and len(tmin_arr) > 1 else DEFAULT_STD,
                models_used=used_models,
                model_divergence=model_divergence,
                # Inter-model std proxies ensemble spread for the regime circuit-breaker.
                ensemble_spread=tmax_std * 2.56,
                ensemble_member_count=len(tmax_arr),
            )
        )

    return results
