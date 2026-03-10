"""
Probability math — forecast → bin probabilities, edge calculation,
Kelly sizing.  Directly from godpaper Section 7.
"""

from __future__ import annotations

import logging

from scipy import stats

from weatheredge.config import TRADING

log = logging.getLogger("weatheredge.probability")


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
    # FIX #3: 0.01 is absurdly tight — a 1°C std dev is the minimum
    # sensible value for a temperature forecast. 0.01 produces near-certain
    # probabilities and wildly inflated edges for any bin containing the mean.
    if forecast_std <= 0:
        log.warning(
            "forecast_to_probabilities: forecast_std=%s is invalid; "
            "clamping to 1.0°C minimum to avoid degenerate distribution.",
            forecast_std,
        )
        forecast_std = 1.0

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

class StaleMarketPricesError(Exception):
    """
    Raised when market prices are so degraded that edge calculation
    would produce meaningless results.

    FIX #1: rather than silently floor bad prices and let execution
    proceed, callers should catch this and skip the market entirely.
    """


def normalise_market_probs(
    prices: list[float],
    *,
    max_zero_fraction: float = 0.5,
) -> list[float]:
    """
    Normalise market implied probabilities so they sum to 1.0.
    Polymarket prices can sum to >1 due to overround.

    Includes a 1% floor and 99% cap to prevent division-by-zero or
    infinite edges in backtesting against resolved markets.

    FIX #1: raises StaleMarketPricesError if the fraction of zero prices
    exceeds ``max_zero_fraction`` (default 50%) so callers can gate on
    data quality before running edge calculations.  Previously the warning
    fired but execution proceeded on fake 1% prices regardless.
    """
    total = sum(prices)
    if total <= 0:
        return [1.0 / len(prices)] * len(prices) if prices else []

    # 1. Primary normalisation
    normed = [p / total for p in prices]

    # 2. Check data quality — warn and optionally gate
    zero_count = sum(1 for p in normed if p <= 0.0)
    if zero_count:
        zero_fraction = zero_count / len(normed)
        log.warning(
            "normalise_market_probs: %d/%d prices are 0.0 before floor is applied. "
            "This almost certainly means PMXT orderbook enrichment failed — "
            "check for 'No orderbook found' warnings above. "
            "Flooring to 1%% as a safety measure; do NOT treat these as real prices.",
            zero_count, len(normed),
        )
        # FIX #1: gate execution if too many prices are missing
        if zero_fraction > max_zero_fraction:
            raise StaleMarketPricesError(
                f"{zero_count}/{len(normed)} market prices are zero — "
                "orderbook enrichment has failed for this market. "
                "Skipping edge calculation to avoid trading on fabricated prices."
            )

    # 3. Apply 1% floor and 99% cap
    floored = [max(min(p, 0.99), 0.01) for p in normed]

    # Re-normalise to ensure sum is exactly 1.0
    final_total = sum(floored)
    return [p / final_total for p in floored]


# =========================================================================
# Edge calculation
# =========================================================================

def calculate_edge(
    model_prob: float,
    market_prob: float,
    market_price: float,
    fee: float = TRADING.polymarket_fee,
    slippage: float = TRADING.assumed_slippage,
    dynamic_slippage: float | None = None,
) -> float:
    """
    Net edge for a single outcome bin, accounting for slippage spread.
    Positive edge → bet YES.  Negative edge → consider NO.

    FIX #2: ``market_price`` is now required so Kelly can compute the
    correct binary payout odds: ``(1 / price) - 1`` rather than always 1.0.

    If dynamic_slippage is provided, it overrides the static slippage default.
    """
    effective_slip = dynamic_slippage if dynamic_slippage is not None else slippage
    raw_edge = model_prob - (market_prob + effective_slip)
    return raw_edge - fee


def calculate_all_edges(
    model_probs: list[float],
    market_probs: list[float],
    market_prices: list[float],
    fee: float = TRADING.polymarket_fee,
    slippage: float = TRADING.assumed_slippage,
    dynamic_slippage: float | None = None,
) -> list[float]:
    """Calculate net edge for every bin."""
    return [
        calculate_edge(mp, mkt, price, fee, slippage, dynamic_slippage)
        for mp, mkt, price in zip(model_probs, market_probs, market_prices)
    ]


# =========================================================================
# Kelly criterion sizing
# =========================================================================

def kelly_fraction(
    edge: float,
    market_price: float,
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
    if odds <= 0:
        return 0.0
    if market_price <= 0 or market_price >= 1:
        return 0.0
    odds = (1.0 / market_price) - 1.0
    full_kelly = edge / odds
    fractional = full_kelly * fraction
    return min(fractional, max_bet)


def recommended_bet_usd(
    edge: float,
    market_price: float,
    bankroll: float,
    fraction: float = TRADING.kelly_fraction,
    max_bet: float = TRADING.kelly_max_bet,
    min_bet_usd: float = 1.0,
) -> float:
    """
    Return dollar amount to bet based on Kelly sizing.

    FIX #2: passes market_price through to kelly_fraction for correct odds.
    Returns 0.0 (not a tiny nonzero amount) when the Kelly fraction is
    below the minimum meaningful bet size.
    """
    frac = kelly_fraction(edge, market_price, fraction, max_bet)
    amount = round(frac * bankroll, 2)
    return amount if amount >= min_bet_usd else 0.0


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