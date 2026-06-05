"""
Tests for the anti-overconfidence / data-integrity hardening:

  - effective_forecast_std       (betting-grade uncertainty inflation)
  - is_model_market_conflict     (model-sanity veto)
  - bins_plausible_for_forecast  (unit-mismatch guard)
  - WeatherEdgeMarketWS.max_size (1009 frame-limit fix)
  - _process_market end-to-end   (the May-29 blowup is now refused, genuine edge survives)
"""

from __future__ import annotations

import math
from datetime import date

from weatheredge.config import TRADING
from weatheredge.models import ForecastResult, ParsedMarket
from weatheredge.pipeline import _process_market
from weatheredge.probability import (
    bins_plausible_for_forecast,
    effective_forecast_std,
    is_model_market_conflict,
)


# =========================================================================
# effective_forecast_std
# =========================================================================

class TestEffectiveForecastStd:
    def test_floors_a_tight_ensemble(self):
        # A deceptively tight ensemble std must never produce a near-degenerate
        # distribution — it is floored.
        assert effective_forecast_std(0.1, lead_time_days=0) >= TRADING.forecast_std_floor

    def test_adds_model_error_in_quadrature(self):
        # sqrt(2^2 + model_error^2) with no lead time.
        expected = math.sqrt(2.0**2 + TRADING.model_error_std**2)
        assert math.isclose(
            effective_forecast_std(2.0, lead_time_days=0), max(expected, TRADING.forecast_std_floor),
            rel_tol=1e-9,
        )

    def test_grows_with_lead_time(self):
        near = effective_forecast_std(2.0, lead_time_days=0)
        far = effective_forecast_std(2.0, lead_time_days=5)
        assert far > near

    def test_never_below_floor_even_with_zero_inputs(self):
        assert effective_forecast_std(0.0, lead_time_days=0, model_error_std=0.0) == TRADING.forecast_std_floor


# =========================================================================
# is_model_market_conflict  (the model-sanity veto)
# =========================================================================

class TestModelMarketConflict:
    def test_flags_overconfident_vs_liquid_market(self):
        # The exact failure mode: model ~100%, liquid market ~6%.
        assert is_model_market_conflict(1.0, 0.06, market_volume=20_000) is True

    def test_exempts_illiquid_market(self):
        # A thin market's price is not trustworthy enough to override the model.
        assert is_model_market_conflict(1.0, 0.06, market_volume=100) is False

    def test_allows_normal_disagreement(self):
        # Moderate confidence vs a moderately-priced bin is normal edge, not a fault.
        assert is_model_market_conflict(0.55, 0.30, market_volume=50_000) is False

    def test_boundary_uses_config_thresholds(self):
        assert is_model_market_conflict(
            TRADING.sanity_model_prob_max, TRADING.sanity_market_prob_min,
            market_volume=TRADING.sanity_min_volume_usd,
        ) is True


# =========================================================================
# bins_plausible_for_forecast  (unit-mismatch guard)
# =========================================================================

class TestBinsPlausible:
    def test_celsius_bins_with_celsius_forecast_ok(self):
        bins = [(float("-inf"), 18), (18, 20), (20, 22), (22, float("inf"))]
        assert bins_plausible_for_forecast(bins, forecast_mean=21.0) is True

    def test_fahrenheit_bins_with_celsius_forecast_rejected(self):
        # 70-90°F bins scored against a ~24°C mean — the March blowup signature.
        bins = [(float("-inf"), 70), (70, 80), (80, 90), (90, float("inf"))]
        assert bins_plausible_for_forecast(bins, forecast_mean=24.0) is False

    def test_pure_yes_no_bins_are_allowed(self):
        # No finite edges to compare against.
        assert bins_plausible_for_forecast([(float("-inf"), float("inf"))], 24.0) is True

    def test_genuinely_cold_day_still_allowed(self):
        bins = [(float("-inf"), 14), (14, 16), (16, float("inf"))]
        assert bins_plausible_for_forecast(bins, forecast_mean=8.0) is True


# =========================================================================
# WebSocket frame-limit fix
# =========================================================================

class TestWebSocketMaxSize:
    def test_default_max_size_well_above_one_mib(self):
        from weatheredge.ws_client import WeatherEdgeMarketWS

        ws = WeatherEdgeMarketWS()
        # Must exceed the 1 MiB (2**20) library default that caused the 1009 loop.
        assert ws.max_size is None or ws.max_size > 2**20


# =========================================================================
# Pipeline end-to-end
# =========================================================================

def _forecast(target: date, mean: float, std: float) -> ForecastResult:
    return ForecastResult(
        station_id="TEST",
        valid_date=target,
        tmax_mean=mean,
        tmax_std=std,
        ensemble_spread=std,
        ensemble_member_count=51,
    )


class TestProcessMarketRobustness:
    def test_may29_blowup_is_refused(self):
        """
        Reproduce the losing trade: a confident model (~100% on the cold-tail
        bin) against a liquid market that prices it at 6%.  The veto must refuse
        it and produce zero signals.
        """
        target = date(2026, 5, 29)
        market = ParsedMarket(
            condition_id="536793",
            question="Highest temperature in Buenos Aires on May 29?",
            outcomes=["14°C or below", "15°C", "16°C", "17°C", "18°C or higher"],
            outcome_prices=[0.06, 0.10, 0.20, 0.40, 0.30],
            token_ids=[],
            volume=20_000.0,
            end_date="",
            description="",
            station_id="SAEZ",
            metric="tmax",
            bins=[
                (float("-inf"), 14.0), (14.0, 15.0), (15.0, 16.0),
                (16.0, 17.0), (17.0, float("inf")),
            ],
            resolution_date=target,
        )
        # Cold, confident forecast → ~100% on bin 0.
        signals, detail = _process_market(
            market, {target: _forecast(target, mean=11.0, std=1.0)}, {},
            None, 10_000.0, eval_date=target,
        )

        assert signals == []
        assert detail.get("model_market_conflicts", 0) >= 1

    def test_genuine_edge_still_produces_a_signal(self):
        """
        A sanity check that the hardening did not disable all trading: a market
        with moderate model confidence and a real mispricing still signals.
        """
        target = date(2026, 6, 4)
        market = ParsedMarket(
            condition_id="600001",
            question="Highest temperature in Wellington on June 4?",
            outcomes=["Under 18", "18-20", "20-22", "22-24", "Over 24"],
            outcome_prices=[0.10, 0.20, 0.25, 0.30, 0.15],
            token_ids=[],
            volume=50_000.0,
            end_date="",
            description="",
            station_id="NZWN",
            metric="tmax",
            bins=[
                (float("-inf"), 18.0), (18.0, 20.0), (20.0, 22.0),
                (22.0, 24.0), (24.0, float("inf")),
            ],
            resolution_date=target,
        )
        signals, _ = _process_market(
            market, {target: _forecast(target, mean=21.0, std=1.5)}, {},
            None, 10_000.0, eval_date=target,
        )

        assert len(signals) >= 1
        # The mispriced 20-22 bin is where the edge lives.
        assert any(s.bin_index == 2 for s in signals)
