"""
Probability math — forecast → bin probabilities, edge calculation,
Kelly sizing.  Directly from godpaper Section 7.
"""

from __future__ import annotations

import logging
import math

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
    # FIX #3: 0.01 is absurdly tight — a degenerate std produces near-certain
    # probabilities and wildly inflated edges for any bin containing the mean.
    # NOTE: callers that bet on these probabilities should pass a std that has
    # already been widened via ``effective_forecast_std``; this clamp is only a
    # last-resort guard against a non-positive value.
    if forecast_std <= 0:
        log.warning(
            "forecast_to_probabilities: forecast_std=%s is invalid; "
            "clamping to %.1f°C minimum to avoid degenerate distribution.",
            forecast_std, TRADING.forecast_std_floor,
        )
        forecast_std = TRADING.forecast_std_floor

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
# Betting-grade uncertainty + sanity guards
# =========================================================================

def effective_forecast_std(
    ensemble_std: float,
    lead_time_days: int = 0,
    *,
    model_error_std: float | None = None,
    per_day: float | None = None,
    floor: float | None = None,
) -> float:
    """
    Widen a raw ensemble std into a betting-grade uncertainty.

    The raw ECMWF ensemble spread is systematically too tight to bet on — it
    repeatedly produced ~100% bin probabilities on outcomes that lost (both
    circuit-breaker blowups).  We combine three independent error sources in
    quadrature and floor the result so a deceptively narrow ensemble can never
    again yield a near-degenerate distribution:

        sqrt(ensemble_std² + model_error_std² + (per_day · lead_time_days)²)

    floored at ``forecast_std_floor``.

    Defaults are read from ``TRADING`` so they stay in one place.
    """
    model_error_std = TRADING.model_error_std if model_error_std is None else model_error_std
    per_day = TRADING.lead_time_std_per_day if per_day is None else per_day
    floor = TRADING.forecast_std_floor if floor is None else floor

    base = max(ensemble_std or 0.0, 0.0)
    lead = max(lead_time_days, 0) * per_day
    inflated = math.sqrt(base * base + model_error_std * model_error_std + lead * lead)
    return max(inflated, floor)


def is_model_market_conflict(
    model_prob: float,
    market_prob: float,
    market_volume: float,
    *,
    model_prob_max: float | None = None,
    market_prob_min: float | None = None,
    min_volume_usd: float | None = None,
) -> bool:
    """
    Detect the failure mode behind both circuit-breaker blowups: the model is
    near-certain about a bin that a *liquid* market prices as very unlikely.

    When a market with real volume disagrees this strongly with the model, the
    model is far more likely to be wrong (stale forecast, unit/bin error, bias)
    than the market.  Callers should treat a ``True`` result as a suspected
    model fault and skip the bin rather than booking the disagreement as edge.

    Illiquid markets are exempt (``market_volume < min_volume_usd``) because a
    thin market's price is not trustworthy enough to override the model.
    """
    model_prob_max = TRADING.sanity_model_prob_max if model_prob_max is None else model_prob_max
    market_prob_min = TRADING.sanity_market_prob_min if market_prob_min is None else market_prob_min
    min_volume_usd = TRADING.sanity_min_volume_usd if min_volume_usd is None else min_volume_usd

    if market_volume < min_volume_usd:
        return False
    return model_prob >= model_prob_max and market_prob <= market_prob_min


def bins_plausible_for_forecast(
    bins: list[tuple[float, float]],
    forecast_mean: float,
    *,
    max_offset_c: float = 25.0,
) -> bool:
    """
    Guard against unit-mismatched bins (Fahrenheit thresholds scored against a
    Celsius forecast).  When 70–90°F bins are compared to a ~24°C mean, the
    normal CDF dumps ~100% of the mass into the cold-tail bin and fabricates an
    enormous edge — the signature of the March blowup.

    Returns ``False`` when the Celsius forecast mean sits more than
    ``max_offset_c`` below the lowest finite bin edge (or above the highest),
    which is physically implausible for a correctly unit-matched market.  A
    legitimate cold/warm day stays comfortably inside this generous window.
    """
    finite_edges = [
        e
        for lo, hi in bins
        for e in (lo, hi)
        if e not in (float("-inf"), float("inf"))
    ]
    if not finite_edges:
        return True  # nothing to compare against (e.g. pure YES/NO)

    if forecast_mean < min(finite_edges) - max_offset_c:
        return False
    if forecast_mean > max(finite_edges) + max_offset_c:
        return False
    return True


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
    fee: float = TRADING.polymarket_fee,
    slippage: float = TRADING.assumed_slippage,
    dynamic_slippage: float | None = None,
) -> float:
    """
    Net edge for a single outcome bin, accounting for slippage and fees.
    Positive edge → bet YES.  Negative edge → consider NO.

    Kelly position sizing uses market_price separately via kelly_fraction().
    If dynamic_slippage is provided, it overrides the static slippage default.
    """
    effective_slip = dynamic_slippage if dynamic_slippage is not None else slippage
    raw_edge = model_prob - (market_prob + effective_slip)
    return raw_edge - fee


def calculate_all_edges(
    model_probs: list[float],
    market_probs: list[float],
    fee: float = TRADING.polymarket_fee,
    slippage: float = TRADING.assumed_slippage,
    dynamic_slippage: float | None = None,
) -> list[float]:
    """Calculate net edge for every bin."""
    return [
        calculate_edge(mp, mkt, fee, slippage, dynamic_slippage)
        for mp, mkt in zip(model_probs, market_probs)
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
    min_bet_usd: float = TRADING.min_bet_usd,
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


def ranked_probability_score(
    predicted_probs: list[float],
    actual_outcome_index: int,
) -> float:
    """
    Ranked Probability Score (RPS) for a single ordered-bin market.

    Unlike the Brier score, RPS is ordinal-aware: near-miss predictions on
    adjacent bins are penalised less than far misses.  Normalised to [0, 1]
    by dividing by (K-1) so it is comparable across markets with different
    numbers of bins.

    Lower is better.  0 = perfect, 1 = worst possible.
    """
    k = len(predicted_probs)
    if k < 2:
        return 0.0
    cum_forecast = 0.0
    score = 0.0
    for i in range(k - 1):
        cum_forecast += predicted_probs[i]
        cum_obs = 1.0 if actual_outcome_index <= i else 0.0
        score += (cum_forecast - cum_obs) ** 2
    return score / (k - 1)


def log_score(
    predicted_probs: list[float],
    actual_outcome_index: int,
    p_min: float = 0.001,
) -> float:
    """
    Logarithmic (Ignorance) score for a single market resolution.

    Measures information gain; directly tied to Kelly wealth growth.
    Strictly proper and locally sensitive — confident mispredictions are
    penalised exponentially more than Brier.

    Lower is better.  0 = perfect (p_correct = 1.0).
    p_min clips the floor to avoid -inf on extreme model failures.
    """
    p = max(predicted_probs[actual_outcome_index], p_min)
    return -math.log2(p)