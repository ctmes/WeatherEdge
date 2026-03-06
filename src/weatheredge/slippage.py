"""
Dynamic slippage estimation — replaces the static assumed_slippage constant.

The Quant: "Polymarket bid/ask spreads are dynamic; treating slippage as a
scalar constant dramatically overstates practical edge in thin markets."

Three estimation tiers:
  1. Order book depth available → walk-the-book simulation
  2. Bid/ask spread available  → half-spread + volume impact
  3. Fallback                  → volume-scaled heuristic
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from weatheredge.config import TRADING


# =========================================================================
# Configuration
# =========================================================================

MIN_SLIPPAGE = 0.005   # 0.5% floor — even the most liquid market has _some_ cost
MAX_SLIPPAGE = 0.10    # 10% ceiling — beyond this we shouldn't be trading at all
DEFAULT_SLIPPAGE = 0.03  # fallback when no market data is available


# =========================================================================
# Core estimation
# =========================================================================

def estimate_slippage(
    bet_size_usd: float,
    market_volume: float = 0.0,
    bid_ask_spread: float | None = None,
    order_book_depth: list[tuple[float, float]] | None = None,
) -> float:
    """
    Estimate realistic execution cost for a given bet size.

    Returns a slippage value in probability-space (e.g., 0.03 = 3%).

    Tier 1: If order_book_depth [(price, qty_usd), ...] is provided,
            simulate walking the book to fill bet_size_usd.
    Tier 2: If bid_ask_spread is provided,
            use half-spread + Kyle's lambda volume impact.
    Tier 3: Fallback to volume-scaled heuristic.
    """
    # Tier 1: Walk-the-book simulation
    if order_book_depth and len(order_book_depth) > 0:
        slippage = _walk_the_book(bet_size_usd, order_book_depth)
        return _clamp(slippage)

    # Tier 2: Spread + volume impact
    if bid_ask_spread is not None and bid_ask_spread > 0:
        half_spread = bid_ask_spread / 2.0
        impact = volume_impact_estimate(bet_size_usd, market_volume)
        slippage = half_spread + impact
        return _clamp(slippage)

    # Tier 3: Volume-scaled heuristic
    if market_volume > 0:
        slippage = _volume_heuristic(bet_size_usd, market_volume)
        return _clamp(slippage)

    # No data at all — use static fallback
    return DEFAULT_SLIPPAGE


def volume_impact_estimate(
    bet_size_usd: float,
    market_volume: float,
) -> float:
    """
    Kyle's lambda model: market impact ∝ bet_size / sqrt(volume).

    For a $500 bet in a $5,000 market: impact ≈ 0.07 (7%)
    For a $500 bet in a $500,000 market: impact ≈ 0.007 (0.7%)

    The coefficient (0.5) is calibrated conservatively for prediction
    markets which are thinner than traditional order books.
    """
    if market_volume <= 0:
        return DEFAULT_SLIPPAGE

    # Kyle's lambda: impact = coefficient * (bet / sqrt(volume))
    # Coefficient chosen so a $300 bet in a $5,000 market ≈ 2% impact
    impact = TRADING.slippage_lambda_coeff * bet_size_usd / math.sqrt(market_volume)

    return max(0.0, impact)


def effective_edge_after_slippage(
    model_prob: float,
    market_prob: float,
    estimated_slippage: float,
    fee: float = 0.02,
) -> float:
    """
    Net edge after realistic execution costs.

    This replaces the old: model_prob - (market_prob + TRADING.assumed_slippage) - fee
    """
    return model_prob - (market_prob + estimated_slippage) - fee


# =========================================================================
# Internals
# =========================================================================

def _walk_the_book(
    bet_size_usd: float,
    depth: list[tuple[float, float]],
) -> float:
    """
    Simulate filling a bet by walking up the ask side of the book.

    depth: [(price, available_usd), ...] sorted best→worst.
    Returns the VWAP slippage (difference between VWAP fill and best price).
    """
    if not depth or bet_size_usd <= 0:
        return DEFAULT_SLIPPAGE

    best_price = depth[0][0]
    remaining = bet_size_usd
    total_cost = 0.0
    total_filled = 0.0

    for price, available in depth:
        if remaining <= 0:
            break
        fill = min(remaining, available)
        total_cost += fill * price
        total_filled += fill
        remaining -= fill

    if total_filled <= 0:
        return DEFAULT_SLIPPAGE

    # If we couldn't fill everything, penalise heavily
    if remaining > 0:
        # Assume the unfilled portion would be at a terrible price
        worst_price = depth[-1][0] if depth else best_price
        penalty_price = worst_price * 1.5  # 50% worse than worst level
        total_cost += remaining * penalty_price
        total_filled += remaining

    vwap = total_cost / total_filled
    slippage = vwap - best_price

    return max(0.0, slippage)


def _volume_heuristic(bet_size_usd: float, market_volume: float) -> float:
    """
    Volume-based slippage heuristic when no spread data is available.

    Uses a log-linear model calibrated so:
      - bet_size/volume = 0.01 → slippage ≈ 1%
      - bet_size/volume = 0.10 → slippage ≈ 3%
      - bet_size/volume = 0.50 → slippage ≈ 7%
    """
    if market_volume <= 0:
        return DEFAULT_SLIPPAGE

    ratio = bet_size_usd / market_volume

    # log-linear: slippage = a + b * ln(1 + ratio * c)
    # Calibrated: a=0.005, b=0.03, c=20
    slippage = 0.005 + 0.03 * math.log(1.0 + ratio * 20.0)

    return slippage


def _clamp(slippage: float) -> float:
    """Enforce floor and ceiling."""
    return max(MIN_SLIPPAGE, min(MAX_SLIPPAGE, slippage))
