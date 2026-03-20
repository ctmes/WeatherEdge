"""
Tests for weatheredge.probability — bin probabilities, edge calculation,
Kelly sizing, and Brier score.
"""

import math

import pytest

from weatheredge.probability import (
    brier_score,
    calculate_all_edges,
    calculate_edge,
    forecast_to_probabilities,
    kelly_fraction,
    normalise_market_probs,
    recommended_bet_usd,
)


# =========================================================================
# forecast_to_probabilities
# =========================================================================

class TestForecastToProbabilities:
    """Normal-CDF bin probability conversion."""

    def test_probabilities_sum_to_one(self):
        bins = [
            (float("-inf"), 18),
            (18, 20),
            (20, 22),
            (22, 24),
            (24, float("inf")),
        ]
        probs = forecast_to_probabilities(21.0, 2.0, bins)
        assert math.isclose(sum(probs), 1.0, abs_tol=1e-6)

    def test_peak_at_mean(self):
        """The bin containing the mean should have the highest probability."""
        bins = [
            (float("-inf"), 18),
            (18, 20),
            (20, 22),
            (22, 24),
            (24, float("inf")),
        ]
        probs = forecast_to_probabilities(21.0, 2.0, bins)
        # 20-22 bin should be highest
        assert probs[2] == max(probs)

    def test_narrow_std_concentrates_probability(self):
        """A very narrow std should put almost all mass in the bin around the mean."""
        bins = [
            (float("-inf"), 20),
            (20, 22),
            (22, float("inf")),
        ]
        probs = forecast_to_probabilities(21.0, 0.1, bins)
        # Nearly all probability in the 20-22 bin
        assert probs[1] > 0.99

    def test_all_probabilities_nonnegative(self):
        bins = [(float("-inf"), 15), (15, 20), (20, float("inf"))]
        probs = forecast_to_probabilities(10.0, 3.0, bins)
        assert all(p >= 0 for p in probs)


# =========================================================================
# normalise_market_probs
# =========================================================================

class TestNormaliseMarketProbs:
    def test_normalises_overround(self):
        prices = [0.10, 0.25, 0.35, 0.25, 0.10]  # sums to 1.05
        normed = normalise_market_probs(prices)
        assert math.isclose(sum(normed), 1.0, abs_tol=1e-6)

    def test_already_normalised(self):
        prices = [0.2, 0.3, 0.5]
        normed = normalise_market_probs(prices)
        assert math.isclose(sum(normed), 1.0, abs_tol=1e-6)

    def test_empty_list(self):
        assert normalise_market_probs([]) == []

    def test_zero_total_returns_uniform(self):
        normed = normalise_market_probs([0, 0, 0])
        assert len(normed) == 3
        assert all(math.isclose(p, 1 / 3, abs_tol=1e-6) for p in normed)

    def test_probability_floor_and_cap(self):
        # 2/3 prices are zero → exceeds the 50% max_zero_fraction guard,
        # which raises StaleMarketPricesError instead of silently flooring.
        import pytest
        from weatheredge.probability import StaleMarketPricesError
        with pytest.raises(StaleMarketPricesError):
            normalise_market_probs([1.0, 0.0, 0.0])

    def test_probability_floor_with_one_zero(self):
        # Only 1/3 prices are zero (below the 50% guard) → floor is applied.
        prices = [0.80, 0.0, 0.15]
        normed = normalise_market_probs(prices)
        assert normed[1] > 0
        assert math.isclose(sum(normed), 1.0, abs_tol=1e-6)


# =========================================================================
# calculate_edge
# =========================================================================

class TestCalculateEdge:
    def test_positive_edge(self):
        edge = calculate_edge(0.70, 0.45, 0.45, fee=0.02, slippage=0.0)
        assert math.isclose(edge, 0.23, abs_tol=1e-6)

    def test_negative_edge(self):
        edge = calculate_edge(0.30, 0.45, 0.45, fee=0.02, slippage=0.0)
        assert edge < 0

    def test_fee_makes_small_edge_negative(self):
        edge = calculate_edge(0.50, 0.49, 0.49, fee=0.02, slippage=0.0)
        assert edge < 0  # 1% raw edge minus 2% fee = -1%

    def test_slippage_makes_small_edge_negative(self):
        edge = calculate_edge(0.53, 0.49, 0.49, fee=0.02, slippage=0.04)
        assert edge < 0  # 4% raw edge, minus 4% slippage, minus 2% fee

    def test_all_edges(self):
        model = [0.10, 0.25, 0.40, 0.20, 0.05]
        market = [0.08, 0.22, 0.35, 0.25, 0.10]
        prices = [0.08, 0.22, 0.35, 0.25, 0.10]
        edges = calculate_all_edges(model, market, prices, fee=0.02, slippage=0.0)
        assert len(edges) == 5


# =========================================================================
# kelly_fraction
# =========================================================================

class TestKelly:
    def test_zero_for_negative_edge(self):
        assert kelly_fraction(-0.05, market_price=0.20) == 0.0

    def test_zero_for_zero_edge(self):
        assert kelly_fraction(0.0, market_price=0.20) == 0.0

    def test_positive_edge(self):
        kf = kelly_fraction(0.10, market_price=0.20, fraction=0.25)
        assert kf > 0
        assert kf <= 0.10  # max cap

    def test_capped_at_max(self):
        # market_price=0.50 → odds=1, full_kelly=0.50, fractional=0.125 > max_bet=0.10
        kf = kelly_fraction(0.50, market_price=0.50, fraction=0.25, max_bet=0.10)
        assert kf == 0.10

    def test_recommended_bet(self):
        bet = recommended_bet_usd(0.10, market_price=0.20, bankroll=10_000, fraction=0.25)
        assert bet > 0
        assert bet <= 1000  # 10% of bankroll


# =========================================================================
# brier_score
# =========================================================================

class TestBrierScore:
    def test_perfect_prediction(self):
        # Predicted perfectly: 100% on the correct outcome
        probs = [0.0, 0.0, 1.0, 0.0, 0.0]
        score = brier_score(probs, actual_outcome_index=2)
        assert math.isclose(score, 0.0, abs_tol=1e-6)

    def test_worst_prediction(self):
        # Predicted 100% on wrong outcome
        probs = [1.0, 0.0, 0.0, 0.0, 0.0]
        score = brier_score(probs, actual_outcome_index=2)
        assert score == 2.0

    def test_uniform_prediction(self):
        probs = [0.2, 0.2, 0.2, 0.2, 0.2]
        score = brier_score(probs, actual_outcome_index=0)
        # (0.2-1)^2 + 4*(0.2-0)^2 = 0.64 + 0.16 = 0.80
        assert math.isclose(score, 0.80, abs_tol=1e-6)
