"""
Station calibration engine — builds and applies bias corrections
per station per season using historical forecast-vs-observation data.

This is the moat. (godpaper Section 6)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

from weatheredge.mesonet import DailyObservation


# =========================================================================
# Calibration data structures
# =========================================================================

@dataclass
class StationBias:
    """
    Computed bias corrections for a single station.
    bias = observation - forecast (positive = model underestimates, negative = overestimates)
    """

    station_id: str
    n_observations: int = 0

    # Seasonal tmax bias (observation - forecast)
    tmax_bias_summer: float = 0.0  # Dec-Feb SH / Jun-Aug NH
    tmax_bias_winter: float = 0.0  # Jun-Aug SH / Dec-Feb NH
    tmax_bias_spring: float = 0.0  # Sep-Nov SH / Mar-May NH
    tmax_bias_autumn: float = 0.0  # Mar-May SH / Sep-Nov NH

    # Seasonal tmin bias
    tmin_bias_summer: float = 0.0
    tmin_bias_winter: float = 0.0
    tmin_bias_spring: float = 0.0
    tmin_bias_autumn: float = 0.0

    # Overall statistics
    tmax_rmse: float = 0.0
    tmin_rmse: float = 0.0
    tmax_mae: float = 0.0
    tmin_mae: float = 0.0

    # Lead-time degradation (°C per day beyond day 1)
    lead_time_degradation: float = 0.15  # default from godpaper


@dataclass
class CalibrationPair:
    """A paired forecast and observation for one station on one day."""

    station_id: str
    valid_date: date
    forecast_tmax: float
    observed_tmax: float | None = None
    forecast_tmin: float | None = None
    observed_tmin: float | None = None
    lead_time_days: int = 1
    season: str = ""  # summer, winter, spring, autumn


# =========================================================================
# Southern/Northern hemisphere season detection
# =========================================================================

_SH_STATIONS = {"NZWN", "YSSY", "YMML"}  # Southern Hemisphere

def get_season(station_id: str, d: date) -> str:
    """Return season name for a given station and date."""
    month = d.month
    is_sh = station_id.upper() in _SH_STATIONS

    if is_sh:
        if month in (12, 1, 2):
            return "summer"
        elif month in (3, 4, 5):
            return "autumn"
        elif month in (6, 7, 8):
            return "winter"
        else:
            return "spring"
    else:
        if month in (12, 1, 2):
            return "winter"
        elif month in (3, 4, 5):
            return "spring"
        elif month in (6, 7, 8):
            return "summer"
        else:
            return "autumn"


# =========================================================================
# Build calibration from paired data
# =========================================================================

def build_calibration(
    pairs: list[CalibrationPair],
) -> StationBias:
    """
    Compute station bias corrections from paired forecast-vs-observation data.

    Returns a StationBias with seasonal bias corrections and error metrics.
    """
    if not pairs:
        return StationBias(station_id="unknown")

    station_id = pairs[0].station_id

    # Group by season
    seasonal_tmax: dict[str, list[float]] = {
        "summer": [], "winter": [], "spring": [], "autumn": []
    }
    seasonal_tmin: dict[str, list[float]] = {
        "summer": [], "winter": [], "spring": [], "autumn": []
    }

    tmax_errors: list[float] = []
    tmin_errors: list[float] = []

    for p in pairs:
        season = p.season or get_season(station_id, p.valid_date)

        if p.observed_tmax is not None:
            diff = p.observed_tmax - p.forecast_tmax  # positive = model too cold
            seasonal_tmax[season].append(diff)
            tmax_errors.append(diff)

        if p.observed_tmin is not None and p.forecast_tmin is not None:
            diff = p.observed_tmin - p.forecast_tmin
            seasonal_tmin[season].append(diff)
            tmin_errors.append(diff)

    def _mean(vals: list[float]) -> float:
        return float(np.mean(vals)) if vals else 0.0

    def _rmse(vals: list[float]) -> float:
        return float(np.sqrt(np.mean(np.array(vals) ** 2))) if vals else 0.0

    def _mae(vals: list[float]) -> float:
        return float(np.mean(np.abs(vals))) if vals else 0.0

    return StationBias(
        station_id=station_id,
        n_observations=len(pairs),
        tmax_bias_summer=_mean(seasonal_tmax["summer"]),
        tmax_bias_winter=_mean(seasonal_tmax["winter"]),
        tmax_bias_spring=_mean(seasonal_tmax["spring"]),
        tmax_bias_autumn=_mean(seasonal_tmax["autumn"]),
        tmin_bias_summer=_mean(seasonal_tmin["summer"]),
        tmin_bias_winter=_mean(seasonal_tmin["winter"]),
        tmin_bias_spring=_mean(seasonal_tmin["spring"]),
        tmin_bias_autumn=_mean(seasonal_tmin["autumn"]),
        tmax_rmse=_rmse(tmax_errors),
        tmin_rmse=_rmse(tmin_errors),
        tmax_mae=_mae(tmax_errors),
        tmin_mae=_mae(tmin_errors),
    )


# =========================================================================
# Apply calibration to a forecast
# =========================================================================

def apply_calibration(
    forecast_mean: float,
    station_bias: StationBias,
    target_date: date,
    metric: str = "tmax",
    lead_time_days: int = 1,
) -> float:
    """
    Apply station-specific bias correction to a raw forecast.

    Returns the calibrated forecast value.
    """
    season = get_season(station_bias.station_id, target_date)

    if metric == "tmax":
        bias_map = {
            "summer": station_bias.tmax_bias_summer,
            "winter": station_bias.tmax_bias_winter,
            "spring": station_bias.tmax_bias_spring,
            "autumn": station_bias.tmax_bias_autumn,
        }
    else:
        bias_map = {
            "summer": station_bias.tmin_bias_summer,
            "winter": station_bias.tmin_bias_winter,
            "spring": station_bias.tmin_bias_spring,
            "autumn": station_bias.tmin_bias_autumn,
        }

    bias = bias_map.get(season, 0.0)

    # Lead-time degradation — uncertainty increases with lead time
    # but we apply bias correction regardless
    calibrated = forecast_mean + bias

    return calibrated


def apply_calibration_std(
    forecast_std: float,
    station_bias: StationBias,
    lead_time_days: int = 1,
) -> float:
    """
    Adjust forecast uncertainty based on station RMSE and lead time.

    Uses the larger of model ensemble spread or historical RMSE,
    increased by lead-time degradation.
    """
    # Use historical RMSE as floor for uncertainty
    floor_std = station_bias.tmax_rmse if station_bias.tmax_rmse > 0 else 2.0
    adjusted = max(forecast_std, floor_std)

    # Lead-time degradation
    if lead_time_days > 1:
        adjusted += station_bias.lead_time_degradation * (lead_time_days - 1)

    return adjusted


# =========================================================================
# Quick calibration builder from observations + forecasts
# =========================================================================

def pair_observations_with_forecasts(
    observations: list[DailyObservation],
    forecasts: dict[date, tuple[float, float | None]],
    station_id: str,
) -> list[CalibrationPair]:
    """
    Pair historical observations with forecast values for calibration.

    forecasts: {date: (tmax_forecast, tmin_forecast)}
    """
    pairs: list[CalibrationPair] = []

    for obs in observations:
        if obs.date in forecasts and obs.tmax_c is not None:
            fc_tmax, fc_tmin = forecasts[obs.date]
            pairs.append(CalibrationPair(
                station_id=station_id,
                valid_date=obs.date,
                forecast_tmax=fc_tmax,
                observed_tmax=obs.tmax_c,
                forecast_tmin=fc_tmin,
                observed_tmin=obs.tmin_c,
                season=get_season(station_id, obs.date),
            ))

    return pairs
