"""
Tests for the dynamic slippage estimation module.
"""

import math
import pytest

from weatheredge.slippage import (
    MIN_SLIPPAGE,
    MAX_SLIPPAGE,
    DEFAULT_SLIPPAGE,
    estimate_slippage,
    volume_impact_estimate,
    effective_edge_after_slippage,
    _walk_the_book,
    _volume_heuristic,
    _clamp,
)


class TestEstimateSlippage:
    """Top-level estimate_slippage dispatcher tests."""

    def test_no_data_returns_default(self):
        """No volume, no spread, no book → static fallback."""
        s = estimate_slippage(bet_size_usd=100.0)
        assert s == DEFAULT_SLIPPAGE

    def test_volume_only_uses_heuristic(self):
        """With volume but no spread → tier 3 heuristic."""
        s = estimate_slippage(bet_size_usd=100.0, market_volume=50_000.0)
        assert MIN_SLIPPAGE <= s <= MAX_SLIPPAGE
        # Should be less than default for a reasonably liquid market
        assert s < DEFAULT_SLIPPAGE

    def test_spread_uses_tier2(self):
        """With spread → tier 2 (half-spread + impact)."""
        s = estimate_slippage(
            bet_size_usd=100.0,
            market_volume=50_000.0,
            bid_ask_spread=0.02,
        )
        assert MIN_SLIPPAGE <= s <= MAX_SLIPPAGE
        # At minimum it should include the half-spread
        assert s >= 0.01

    def test_order_book_uses_tier1(self):
        """With order book depth → tier 1 walk-the-book."""
        depth = [(0.50, 200.0), (0.52, 300.0), (0.55, 500.0)]
        s = estimate_slippage(
            bet_size_usd=100.0,
            order_book_depth=depth,
        )
        assert MIN_SLIPPAGE <= s <= MAX_SLIPPAGE
        # $100 fill at first level entirely → slippage = 0
        # But clamped to MIN_SLIPPAGE
        assert s == MIN_SLIPPAGE

    def test_large_bet_more_slippage(self):
        """Bigger bet → more slippage (all tiers)."""
        small = estimate_slippage(bet_size_usd=50.0, market_volume=10_000.0)
        large = estimate_slippage(bet_size_usd=500.0, market_volume=10_000.0)
        assert large > small

    def test_thin_market_more_slippage(self):
        """Thinner market → more slippage."""
        liquid = estimate_slippage(bet_size_usd=100.0, market_volume=100_000.0)
        thin = estimate_slippage(bet_size_usd=100.0, market_volume=1_000.0)
        assert thin > liquid

    def test_always_clamped(self):
        """Output is always within [MIN, MAX]."""
        # Absurdly large bet in tiny market
        s = estimate_slippage(bet_size_usd=100_000.0, market_volume=10.0)
        assert s == MAX_SLIPPAGE

        # Tiny bet in massive market
        s = estimate_slippage(bet_size_usd=0.01, market_volume=10_000_000.0)
        assert s == pytest.approx(MIN_SLIPPAGE, abs=1e-6)


class TestVolumeImpact:
    """Kyle's lambda model tests."""

    def test_zero_volume_returns_default(self):
        assert volume_impact_estimate(100.0, 0.0) == DEFAULT_SLIPPAGE

    def test_impact_scales_with_bet_size(self):
        small = volume_impact_estimate(100.0, 50_000.0)
        large = volume_impact_estimate(1000.0, 50_000.0)
        assert large > small

    def test_impact_decreases_with_volume(self):
        thin = volume_impact_estimate(100.0, 5_000.0)
        liquid = volume_impact_estimate(100.0, 500_000.0)
        assert thin > liquid


class TestWalkTheBook:
    """Order book simulation tests."""

    def test_empty_book(self):
        assert _walk_the_book(100.0, []) == DEFAULT_SLIPPAGE

    def test_zero_bet(self):
        depth = [(0.50, 200.0)]
        assert _walk_the_book(0.0, depth) == DEFAULT_SLIPPAGE

    def test_fills_at_best_price(self):
        """Bet fully at first level → zero slippage."""
        depth = [(0.50, 500.0)]
        s = _walk_the_book(100.0, depth)
        assert s == pytest.approx(0.0)

    def test_walks_multiple_levels(self):
        """Bet requires multiple levels → positive slippage."""
        depth = [(0.50, 50.0), (0.52, 50.0), (0.55, 100.0)]
        s = _walk_the_book(100.0, depth)
        # VWAP: (50*0.50 + 50*0.52) / 100 = 0.51, slippage = 0.51 - 0.50 = 0.01
        assert s == pytest.approx(0.01)

    def test_insufficient_depth_penalises(self):
        """Can't fill entirely → heavy penalty on unfilled portion."""
        depth = [(0.50, 30.0)]
        s = _walk_the_book(100.0, depth)
        # Unfilled 70 units at 0.50 * 1.5 = 0.75
        # VWAP: (30*0.50 + 70*0.75) / 100 = 0.675, slippage = 0.175
        assert s > 0.1


class TestVolumeHeuristic:
    """Log-linear heuristic tests."""

    def test_zero_volume(self):
        assert _volume_heuristic(100.0, 0.0) == DEFAULT_SLIPPAGE

    def test_small_ratio_low_slippage(self):
        """bet/volume = 0.01 → ~1% slippage."""
        s = _volume_heuristic(100.0, 10_000.0)
        assert 0.005 < s < 0.03

    def test_large_ratio_high_slippage(self):
        """bet/volume = 0.5 → ~7% slippage."""
        s = _volume_heuristic(5_000.0, 10_000.0)
        assert s > 0.05


class TestEffectiveEdge:
    """Net edge calculation after slippage."""

    def test_positive_edge(self):
        e = effective_edge_after_slippage(0.60, 0.40, 0.03, 0.02)
        # 0.60 - (0.40 + 0.03) - 0.02 = 0.15
        assert e == pytest.approx(0.15)

    def test_negative_edge(self):
        e = effective_edge_after_slippage(0.40, 0.40, 0.03, 0.02)
        # 0.40 - (0.40 + 0.03) - 0.02 = -0.05
        assert e == pytest.approx(-0.05)

    def test_high_slippage_kills_edge(self):
        e = effective_edge_after_slippage(0.55, 0.45, 0.08, 0.02)
        # 0.55 - (0.45 + 0.08) - 0.02 = 0.00
        assert e == pytest.approx(0.00)


class TestClamp:
    """Floor/ceiling enforcement."""

    def test_below_min(self):
        assert _clamp(0.001) == MIN_SLIPPAGE

    def test_above_max(self):
        assert _clamp(0.50) == MAX_SLIPPAGE

    def test_in_range_unchanged(self):
        assert _clamp(0.04) == 0.04
