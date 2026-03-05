"""
Station calibration engine — builds and applies bias corrections
per station per season using historical forecast-vs-observation data.

This is the moat. (godpaper Section 6)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import logging
import numpy as np

from weatheredge.mesonet import DailyObservation
from weatheredge.cache import cache_key as _cache_key, read_cache, write_cache

logger = logging.getLogger("weatheredge.calibration")
_SUBDIR = "calibration_cache"


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

    # Statistical significance (p-values) per season
    # Higher p-value = bias is likely noise. p < 0.05 is standard significance.
    tmax_p_values: dict[str, float] = field(default_factory=dict)
    tmin_p_values: dict[str, float] = field(default_factory=dict)
    
    # Standard Errors per season
    tmax_std_errors: dict[str, float] = field(default_factory=dict)
    tmin_std_errors: dict[str, float] = field(default_factory=dict)

    # Sample sizes per season
    seasonal_counts: dict[str, int] = field(default_factory=dict)

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
    import scipy.stats as stats

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

    def _stats(vals: list[float]) -> tuple[float, float]:
        """Return (p_value, std_error) if enough data, else (1.0, 0.0)"""
        if len(vals) < 2:
            return 1.0, 0.0
        t_stat, p_val = stats.ttest_1samp(vals, 0)
        std_err = float(np.std(vals, ddof=1) / np.sqrt(len(vals)))
        return float(p_val), std_err

    p_tmax, se_tmax = {}, {}
    p_tmin, se_tmin = {}, {}
    counts = {}

    for s in ["summer", "winter", "spring", "autumn"]:
        p_tmax[s], se_tmax[s] = _stats(seasonal_tmax[s])
        p_tmin[s], se_tmin[s] = _stats(seasonal_tmin[s])
        counts[s] = len(seasonal_tmax[s])

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
        tmax_p_values=p_tmax,
        tmin_p_values=p_tmin,
        tmax_std_errors=se_tmax,
        tmin_std_errors=se_tmin,
        seasonal_counts=counts,
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


# =========================================================================
# Standalone Runner
# =========================================================================

async def run_calibration(
    station_id: str,
    days: int = 365,
    end_date: date | None = None,
) -> tuple[StationBias, dict[str, Any]]:
    """
    Standalone runner for station calibration (godpaper Section 6 / Quant Audit).
    
    1. Fetch historical observations (Mesonet)
    2. Fetch historical forecasts (backtest_fetchers)
    3. Split into Train (80%) and Test (20%) sets
    4. Build calibration on Train set
    5. Evaluate on Test set (MSE improvement)
    
    Returns (StationBias, metrics_dict).
    """
    import asyncio
    import numpy as np
    from datetime import date, timedelta, datetime
    from weatheredge.calibration import StationBias, CalibrationPair, build_calibration, apply_calibration
    from weatheredge.backtest_fetchers import fetch_historical_forecast_ensemble
    from weatheredge.mesonet import fetch_daily_observations
    from weatheredge.stations import get_station

    end = end_date or (date.today() - timedelta(days=1))
    
    ck = _cache_key("calibration", station_id, str(days), end.isoformat())
    cached = read_cache(_SUBDIR, ck, max_age_hours=24.0)
    if cached is not None:
        try:
            bias_dict = cached["bias"]
            # Reconstruct StationBias dataclass
            bias = StationBias(**bias_dict)
            return bias, cached["metrics"]
        except Exception as e:
            logger.debug(f"Failed to load cached calibration for {station_id}: {e}")

    station = get_station(station_id)
    if not station:
        raise ValueError(f"Unknown station: {station_id}")

    start = end - timedelta(days=days)

    logger.info("Fetching %d days of data for %s...", days, station_id)
    observations = await fetch_daily_observations(station_id, start, end)

    if not observations:
        return StationBias(station_id=station_id), {"error": "No observations found"}

    logger.info("Retrieved %d daily observations.", len(observations))

    # Concurrency control: Limit to 5 simultaneous API requests
    sem = asyncio.Semaphore(5)

    async def _fetch_with_sem(lat, lon, dt, tz):
        async with sem:
            return await fetch_historical_forecast_ensemble(lat, lon, dt, timezone=tz)

    # Gather forecast fetches in parallel with semaphore pacing
    forecast_tasks = [
        _fetch_with_sem(
            station.latitude, station.longitude, o.date, station.timezone
        )
        for o in observations
    ]

    logger.info("Fetching %d historical forecasts (pacing with semaphore)...", len(forecast_tasks))
    forecasts_results = await asyncio.gather(*forecast_tasks)

    pairs = []
    for obs, fc in zip(observations, forecasts_results):
        if fc and obs.tmax_c is not None:
            pairs.append(CalibrationPair(
                station_id=station_id,
                valid_date=obs.date,
                forecast_tmax=fc.tmax_mean,
                observed_tmax=obs.tmax_c,
                forecast_tmin=fc.tmin_mean,
                observed_tmin=obs.tmin_c,
                lead_time_days=1,
            ))

    if not pairs:
        return StationBias(station_id=station_id), {"error": "No paired data found"}

    # Split Train/Test (80/20) — sorted by date
    pairs.sort(key=lambda x: x.valid_date)
    split_idx = int(len(pairs) * 0.8)
    train_pairs = pairs[:split_idx]
    test_pairs = pairs[split_idx:]

    bias = build_calibration(train_pairs)

    # ── OOS Evaluation ──────────────────────────────────────────
    # Quant Audit: Verify that bias reduces MSE on holdout set.
    def _get_mse(p_list: list[CalibrationPair], b: StationBias | None = None) -> float:
        errors = []
        for p in p_list:
            fc = p.forecast_tmax
            if b:
                fc = apply_calibration(fc, b, p.valid_date, metric="tmax")
            errors.append((p.observed_tmax - fc) ** 2)
        return float(np.mean(errors)) if errors else 0.0

    raw_mse = _get_mse(test_pairs)
    cal_mse = _get_mse(test_pairs, bias)
    improvement = (raw_mse - cal_mse) / raw_mse if raw_mse > 0 else 0.0

    metrics = {
        "train_size": len(train_pairs),
        "test_size": len(test_pairs),
        "raw_oos_mse": raw_mse,
        "cal_oos_mse": cal_mse,
        "oos_improvement_pct": improvement * 100,
        "is_valid": improvement > 0,
    }

    # Cache the result
    import dataclasses
    write_cache(_SUBDIR, ck, {
        "bias": dataclasses.asdict(bias),
        "metrics": metrics
    })

    return bias, metrics


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m weatheredge.calibration <station_id>")
        sys.exit(1)
        
    logging.basicConfig(level=logging.INFO)
    sid = sys.argv[1]
    b, m = asyncio.run(run_calibration(sid))
    
    print("\n" + "="*40)
    print(f" Calibration Report: {sid}")
    print("="*40)
    print(f"Train Size: {m['train_size']}")
    print(f"Test Size:  {m['test_size']}")
    print(f"OOS Imprvt: {m['oos_improvement_pct']:.1f}%")
    print("-" * 40)
    print(f"Summer Bias: {b.tmax_bias_summer:+.2f}C (p={b.tmax_p_values.get('summer', 1.0):.3f})")
    print(f"Winter Bias: {b.tmax_bias_winter:+.2f}C (p={b.tmax_p_values.get('winter', 1.0):.3f})")
    print(f"Spring Bias: {b.tmax_bias_spring:+.2f}C (p={b.tmax_p_values.get('spring', 1.0):.3f})")
    print(f"Autumn Bias: {b.tmax_bias_autumn:+.2f}C (p={b.tmax_p_values.get('autumn', 1.0):.3f})")
    print("="*40)
