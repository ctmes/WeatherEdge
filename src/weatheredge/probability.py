"""
Probability math — forecast → bin probabilities, edge calculation,
Kelly sizing.  Directly from godpaper Section 7.
"""

from __future__ import annotations

from scipy import stats

from weatheredge.config import TRADING


# =========================================================================
# Forecast → Probabilities
# =========================================================================

def forecast_to_probabilities(
    forecast_mean: float,
    forecast_std: float,
    bins: list[tuple[float, float]],
) -> list[float]:
    """
    Convert a forecast mean and standard deviation to probabilities
    for each market bin using a normal CDF.

    bins: list of (low, high) tuples, e.g.
          [(-inf, 18), (18, 20), (20, 22), (22, 24), (24, inf)]
    """
    if forecast_std <= 0:
        forecast_std = 0.01  # avoid degenerate distribution

    distribution = stats.norm(loc=forecast_mean, scale=forecast_std)
    probabilities: list[float] = []

    for low, high in bins:
        if low == float("-inf"):
            prob = distribution.cdf(high)
        elif high == float("inf"):
            prob = 1.0 - distribution.cdf(low)
        else:
            prob = distribution.cdf(high) - distribution.cdf(low)
        probabilities.append(max(prob, 0.0))

    return probabilities


# =========================================================================
# Market price normalisation
# =========================================================================

def normalise_market_probs(prices: list[float]) -> list[float]:
    """
    Normalise market implied probabilities so they sum to 1.0.
    Polymarket prices can sum to >1 due to overround.
    """
    total = sum(prices)
    if total <= 0:
        return [1.0 / len(prices)] * len(prices) if prices else []
    return [p / total for p in prices]


# =========================================================================
# Edge calculation
# =========================================================================

def calculate_edge(
    model_prob: float,
    market_prob: float,
    fee: float = TRADING.polymarket_fee,
    slippage: float = TRADING.assumed_slippage,
) -> float:
    """
    Net edge for a single outcome bin, accounting for slippage spread.
    Positive edge → bet YES.  Negative edge → consider NO.
    """
    raw_edge = model_prob - (market_prob + slippage)
    return raw_edge - fee


def calculate_all_edges(
    model_probs: list[float],
    market_probs: list[float],
    fee: float = TRADING.polymarket_fee,
    slippage: float = TRADING.assumed_slippage,
) -> list[float]:
    """Calculate net edge for every bin."""
    return [
        calculate_edge(mp, mkt, fee, slippage)
        for mp, mkt in zip(model_probs, market_probs)
    ]


# =========================================================================
# Kelly criterion sizing
# =========================================================================

def kelly_fraction(
    edge: float,
    odds: float = 1.0,
    fraction: float = TRADING.kelly_fraction,
    max_bet: float = TRADING.kelly_max_bet,
) -> float:
    """
    Fractional Kelly criterion for position sizing.

    edge:     net edge (after fees)
    odds:     payout odds (1.0 for binary Polymarket outcomes)
    fraction: Kelly fraction (0.25 = quarter Kelly)
    max_bet:  hard cap as fraction of bankroll
    """
    if edge <= 0:
        return 0.0
    full_kelly = edge / odds
    fractional = full_kelly * fraction
    return min(fractional, max_bet)


def recommended_bet_usd(
    edge: float,
    bankroll: float,
    odds: float = 1.0,
    fraction: float = TRADING.kelly_fraction,
    max_bet: float = TRADING.kelly_max_bet,
) -> float:
    """Return dollar amount to bet based on Kelly sizing."""
    frac = kelly_fraction(edge, odds, fraction, max_bet)
    return round(frac * bankroll, 2)


# =========================================================================
# Brier score
# =========================================================================

def brier_score(
    predicted_probs: list[float],
    actual_outcome_index: int,
) -> float:
    """
    Brier score for a single market resolution.
    Lower is better.  0 = perfect, 2 = worst possible.
    """
    n = len(predicted_probs)
    actual = [0.0] * n
    actual[actual_outcome_index] = 1.0
    return sum((p - a) ** 2 for p, a in zip(predicted_probs, actual))
