"""
Atmospheric regime flags — from the Meteorologist persona document.

Implements automated safety checks that prevent betting in dangerous
forecast regimes where model skill is low.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from weatheredge.config import TRADING
from weatheredge.forecast import AtmosphericRegime


# =========================================================================
# Flag definitions
# =========================================================================

@dataclass
class RegimeAssessment:
    """Result of atmospheric regime assessment for one station-day."""

    station_id: str
    flags: list[str] = field(default_factory=list)
    confidence_multiplier: float = 1.0
    can_trade: bool = True
    reason: str = ""


# =========================================================================
# Station-specific wind risk zones (from meteorologist persona)
# =========================================================================

# Wind direction ranges (degrees) that are dangerous for specific stations
_STATION_WIND_RISKS: dict[str, dict[str, Any]] = {
    "NZWN": {
        "name": "Wellington Southerly Risk",
        "flag": "WELLINGTON_SOUTHERLY_RISK",
        "wind_dir_range": (170, 220),  # southerly quadrant
        "multiplier": 0.3,
    },
    "YSSY": {
        "name": "Sydney Westerly (No Sea Breeze)",
        "flag": "SYDNEY_WESTERLY_RISK",
        "wind_dir_range": (240, 310),  # westerly → sea breeze failure
        "multiplier": 0.5,
    },
    "YMML": {
        "name": "Melbourne Cool Change Risk",
        "flag": "MELBOURNE_COOL_CHANGE_RISK",
        "wind_dir_range": (200, 260),  # SW changes
        "multiplier": 0.4,
    },
}


# =========================================================================
# Public API
# =========================================================================

def assess_regime(
    regime: AtmosphericRegime,
    gfs_tmax: float | None = None,
    ecmwf_tmax: float | None = None,
    ensemble_spread: float | None = None,
    lead_time_hours: int = 48,
) -> RegimeAssessment:
    """
    Assess atmospheric regime flags for a single station-day.

    This implements the checks from the meteorologist persona:
    - Model divergence (GFS vs ECMWF >3°C → no bet)
    - Ensemble spread (>6°C → no bet, >4°C → reduce sizing)
    - Convective risk (CAPE thresholds)
    - Station-specific wind risks
    - Lead-time degradation

    Returns a RegimeAssessment with flags, confidence multiplier, and tradability.
    """
    result = RegimeAssessment(station_id=regime.station_id)

    # ── Model divergence ──────────────────────────────────────────
    if gfs_tmax is not None and ecmwf_tmax is not None:
        divergence = abs(gfs_tmax - ecmwf_tmax)
        regime.model_divergence = divergence

        if divergence > TRADING.model_divergence_no_bet:
            result.flags.append("MODEL_DIVERGENCE_HIGH")
            result.confidence_multiplier = 0.0
            result.can_trade = False
            result.reason = (
                f"GFS/ECMWF divergence {divergence:.1f}°C "
                f"exceeds {TRADING.model_divergence_no_bet}°C threshold"
            )
            return result  # hard stop

    # ── Ensemble spread ───────────────────────────────────────────
    spread = ensemble_spread or regime.ensemble_spread
    if spread > TRADING.ensemble_spread_no_bet:
        result.flags.append("ENSEMBLE_SPREAD_VERY_HIGH")
        result.confidence_multiplier = 0.0
        result.can_trade = False
        result.reason = (
            f"Ensemble spread {spread:.1f}°C "
            f"exceeds {TRADING.ensemble_spread_no_bet}°C threshold"
        )
        return result
    elif spread > TRADING.ensemble_spread_reduce:
        result.flags.append("ENSEMBLE_SPREAD_HIGH")
        result.confidence_multiplier *= 0.5

    # ── Convective risk (CAPE) ────────────────────────────────────
    if regime.cape_max is not None:
        if regime.cape_max > 1000:
            result.flags.append("CONVECTIVE_RISK_HIGH")
            result.confidence_multiplier *= 0.25
        elif regime.cape_max > 500:
            result.flags.append("CONVECTIVE_RISK_MODERATE")
            result.confidence_multiplier *= 0.5

    # ── Station-specific wind risks ───────────────────────────────
    station_risk = _STATION_WIND_RISKS.get(regime.station_id)
    if station_risk and regime.wind_dir_850hpa is not None:
        low, high = station_risk["wind_dir_range"]
        if low <= regime.wind_dir_850hpa <= high:
            result.flags.append(station_risk["flag"])
            result.confidence_multiplier *= station_risk["multiplier"]

    # ── Lead-time degradation ─────────────────────────────────────
    if lead_time_hours > 120:
        result.flags.append("LONG_LEAD_TIME")
        result.confidence_multiplier *= 0.6
    elif lead_time_hours > 72:
        result.flags.append("MODERATE_LEAD_TIME")
        result.confidence_multiplier *= 0.8

    # ── Final assessment ──────────────────────────────────────────
    if result.confidence_multiplier <= 0:
        result.can_trade = False

    if not result.reason and result.flags:
        result.reason = f"Flags active: {', '.join(result.flags)}"

    return result


def adjust_edge_threshold(
    base_threshold: float,
    regime: RegimeAssessment,
) -> float:
    """
    Increase the minimum edge threshold in uncertain regimes.
    Meteorologist persona: double threshold when ensemble spread is high.
    """
    if "ENSEMBLE_SPREAD_HIGH" in regime.flags:
        return base_threshold * 2  # 5% → 10%
    if "CONVECTIVE_RISK_HIGH" in regime.flags:
        return base_threshold * 2
    if "CONVECTIVE_RISK_MODERATE" in regime.flags:
        return base_threshold * 1.5
    return base_threshold
