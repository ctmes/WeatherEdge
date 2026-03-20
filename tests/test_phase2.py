"""
Tests for the calibration engine, regime flags, and safety guards.
"""

import math
from datetime import date, datetime, timedelta

import pytest

from weatheredge.calibration import (
    CalibrationPair,
    StationBias,
    apply_calibration,
    apply_calibration_std,
    build_calibration,
    get_season,
)
from weatheredge.forecast import AtmosphericRegime
from weatheredge.regime import RegimeAssessment, assess_regime, adjust_edge_threshold
from weatheredge.safety import (
    CircuitBreaker,
    ExecutionGuard,
    ExecutionRateLimitError,
    PositionLimits,
    StaleDataError,
    check_position_limits,
    validate_data_freshness,
)


# =========================================================================
# Calibration
# =========================================================================

class TestSeason:
    def test_nz_winter_is_july(self):
        assert get_season("NZWN", date(2026, 7, 15)) == "winter"

    def test_nz_summer_is_january(self):
        assert get_season("NZWN", date(2026, 1, 15)) == "summer"

    def test_us_winter_is_january(self):
        assert get_season("KORD", date(2026, 1, 15)) == "winter"

    def test_us_summer_is_july(self):
        assert get_season("KORD", date(2026, 7, 15)) == "summer"

    def test_au_autumn_is_march(self):
        assert get_season("YSSY", date(2026, 3, 15)) == "autumn"


class TestBuildCalibration:
    def test_computes_bias(self):
        pairs = [
            CalibrationPair(
                station_id="NZWN",
                valid_date=date(2026, 1, i),
                forecast_tmax=20.0,
                observed_tmax=18.5,
                season="summer",
            )
            for i in range(1, 31)  # 30 summer days, all 1.5°C cooler
        ]
        bias = build_calibration(pairs)
        assert bias.station_id == "NZWN"
        assert bias.n_observations == 30
        # Bias should be negative (observed - forecast = -1.5)
        assert math.isclose(bias.tmax_bias_summer, -1.5, abs_tol=0.01)

    def test_empty_pairs(self):
        bias = build_calibration([])
        assert bias.n_observations == 0

    def test_rmse_positive(self):
        pairs = [
            CalibrationPair(
                station_id="KORD",
                valid_date=date(2026, 7, i),
                forecast_tmax=30.0,
                observed_tmax=30.0 + (i % 3 - 1),  # -1, 0, +1 pattern
                season="summer",
            )
            for i in range(1, 31)
        ]
        bias = build_calibration(pairs)
        assert bias.tmax_rmse > 0


class TestApplyCalibration:
    def test_applies_bias(self):
        bias = StationBias(
            station_id="NZWN",
            tmax_bias_summer=-1.5,
            tmax_p_values={"summer": 0.05},  # statistically significant → bias applied
        )
        calibrated = apply_calibration(
            forecast_mean=22.0,
            station_bias=bias,
            target_date=date(2026, 1, 15),  # NZ summer
            metric="tmax",
        )
        assert math.isclose(calibrated, 20.5, abs_tol=0.01)

    def test_std_uses_rmse_floor(self):
        bias = StationBias(station_id="KORD", tmax_rmse=2.5)
        adjusted = apply_calibration_std(1.0, bias)  # model says 1.0 std
        assert adjusted == 2.5  # RMSE floor is higher


# =========================================================================
# Regime flags
# =========================================================================

class TestRegimeFlags:
    def test_model_divergence_blocks_trade(self):
        regime = AtmosphericRegime(station_id="NZWN", valid_date=date(2026, 3, 5))
        result = assess_regime(regime, gfs_tmax=25.0, ecmwf_tmax=20.0)
        assert not result.can_trade
        assert "MODEL_DIVERGENCE_HIGH" in result.flags

    def test_high_ensemble_spread_blocks(self):
        regime = AtmosphericRegime(
            station_id="NZWN",
            valid_date=date(2026, 3, 5),
            ensemble_spread=7.0,
        )
        result = assess_regime(regime)
        assert not result.can_trade
        assert "ENSEMBLE_SPREAD_VERY_HIGH" in result.flags

    def test_moderate_spread_reduces_sizing(self):
        regime = AtmosphericRegime(
            station_id="NZWN",
            valid_date=date(2026, 3, 5),
            ensemble_spread=5.0,
        )
        result = assess_regime(regime)
        assert result.can_trade
        assert result.confidence_multiplier < 1.0

    def test_high_cape_flags_convective(self):
        regime = AtmosphericRegime(
            station_id="YSSY",
            valid_date=date(2026, 1, 15),
            cape_max=1500,
        )
        result = assess_regime(regime)
        assert "CONVECTIVE_RISK_HIGH" in result.flags
        assert result.confidence_multiplier <= 0.25

    def test_clean_regime_full_confidence(self):
        regime = AtmosphericRegime(
            station_id="EGLL",
            valid_date=date(2026, 1, 15),
            ensemble_spread=1.5,
            cape_max=100,
        )
        result = assess_regime(regime, gfs_tmax=10.0, ecmwf_tmax=10.5)
        assert result.can_trade
        assert result.confidence_multiplier == 1.0
        assert len(result.flags) == 0

    def test_wellington_southerly(self):
        regime = AtmosphericRegime(
            station_id="NZWN",
            valid_date=date(2026, 3, 5),
            wind_dir_850hpa=190,
        )
        result = assess_regime(regime)
        assert "WELLINGTON_SOUTHERLY_RISK" in result.flags

    def test_edge_threshold_doubles_for_spread(self):
        regime = RegimeAssessment(
            station_id="NZWN",
            flags=["ENSEMBLE_SPREAD_HIGH"],
        )
        threshold = adjust_edge_threshold(0.05, regime)
        assert threshold == 0.10


# =========================================================================
# Safety guards
# =========================================================================

class TestDataFreshness:
    def test_fresh_data_passes(self):
        now = datetime.utcnow()
        validate_data_freshness(now - timedelta(minutes=5))  # should not raise

    def test_stale_data_raises(self):
        old = datetime.utcnow() - timedelta(minutes=30)
        with pytest.raises(StaleDataError):
            validate_data_freshness(old)


class TestExecutionGuard:
    def test_allows_first_order(self):
        guard = ExecutionGuard()
        assert guard.can_execute("market_1") is True

    def test_blocks_after_rate_limit(self):
        guard = ExecutionGuard(max_orders_per_minute=2)
        guard.record_execution("m1")
        guard.record_execution("m2")
        with pytest.raises(ExecutionRateLimitError):
            guard.can_execute("m3")


class TestPositionLimits:
    def test_allows_within_limits(self):
        ok, msg = check_position_limits(
            proposed_bet_usd=200,
            bankroll=10_000,
            current_market_exposure=0,
            current_total_exposure=1000,
        )
        assert ok

    def test_blocks_bin_limit(self):
        ok, msg = check_position_limits(
            proposed_bet_usd=500,  # 5% > 3% limit
            bankroll=10_000,
            current_market_exposure=0,
            current_total_exposure=0,
        )
        assert not ok
        assert "single bin" in msg

    def test_blocks_total_limit(self):
        ok, msg = check_position_limits(
            proposed_bet_usd=100,
            bankroll=10_000,
            current_market_exposure=0,
            current_total_exposure=4_950,  # already near 50%
        )
        assert not ok


class TestCircuitBreaker:
    def test_ok_when_no_losses(self):
        cb = CircuitBreaker()
        status, msg = cb.check(10_000, 0, 0, 0, 10_000)
        assert status == "OK"

    def test_pause_on_daily_loss(self):
        cb = CircuitBreaker()
        status, _ = cb.check(10_000, -600, 0, 0, 10_000)
        assert status == "PAUSE"

    def test_shutdown_on_drawdown(self):
        cb = CircuitBreaker()
        status, _ = cb.check(7_000, 0, 0, 0, 10_000)
        assert status == "SHUTDOWN"
