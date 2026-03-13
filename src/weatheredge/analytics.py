"""
Quantitative Analytics — Brier Skill Score, confidence intervals, 
calibration diagrams, and risk-adjusted metrics. 
Directly from The Quant persona specifications.
"""

from __future__ import annotations

import math
import numpy as np
import scipy.stats as stats
from typing import Any


# =========================================================================
# Probabilistic Scoring
# =========================================================================

def brier_skill_score(model_brier: float, baseline_brier: float) -> float:
    """
    BSS > 0: Model is better than baseline
    BSS = 1: Perfect model
    BSS = 0: No better than baseline
    BSS < 0: Worse than baseline
    """
    if baseline_brier == 0:
        return 0.0 if model_brier == 0 else -float("inf")
    return 1 - (model_brier / baseline_brier)


def calculate_climatological_baseline(bins: list[tuple[float, float]], hist_mean: float, hist_std: float) -> float:
    """
    Calculate the Brier Score our model would get if it just guessed 
    probabilities based on historical averages (Section 7.2 of Godpaper).
    Used as the 'Reference Brier' for BSS.
    """
    from weatheredge.probability import forecast_to_probabilities
    
    # We assume 'truth' is the historical mean for this baseline calculation
    # to find what the BS would be for a naive forecast.
    probs = forecast_to_probabilities(hist_mean, hist_std, bins)
    
    # Simple multi-category Brier: sum of squared errors
    # In a theoretical baseline, we can use the sum of squares of the probabilities
    # as an expected Brier score if the outcomes follow the historical distribution.
    return float(np.sum(np.square(probs)))


def expected_calibration_error(
    predicted_probs: list[float],
    outcomes: list[int],
    n_bins: int = 10,
) -> float:
    """
    Expected Calibration Error (ECE): sample-weighted mean absolute gap
    between predicted confidence and actual hit rate, across n_bins buckets.

    predicted_probs: probability assigned to the 'true' outcome for each market
    outcomes: 1 (correct) or 0 (incorrect) for each prediction
    Returns ECE in [0, 1]; 0 = perfect calibration.
    """
    diagram = reliability_diagram_data(predicted_probs, outcomes, n_bins=n_bins)
    if not diagram:
        return 0.0
    total = sum(d["count"] for d in diagram)
    if total == 0:
        return 0.0
    return float(sum(
        (d["count"] / total) * abs(d["predicted_mean"] - d["actual_frequency"])
        for d in diagram
    ))


def reliability_diagram_data(
    predicted_probs: list[float], 
    outcomes: list[int], 
    n_bins: int = 10
) -> list[dict[str, Any]]:
    """
    Group predictions into bins, compare predicted vs actual frequency.
    
    predicted_probs: list of probability for the 'true' outcome index
    outcomes: list of 1 (won) or 0 (lost)
    """
    if not predicted_probs or not outcomes:
        return []
        
    p_array = np.array(predicted_probs)
    o_array = np.array(outcomes)
    
    bins = np.linspace(0, 1, n_bins + 1)
    bin_data = []
    
    for i in range(n_bins):
        low, high = bins[i], bins[i+1]
        mask = (p_array >= low) & (p_array < high)
        if mask.any():
            bin_data.append({
                'bin_center': (low + high) / 2,
                'predicted_mean': p_array[mask].mean(),
                'actual_frequency': o_array[mask].mean(),
                'count': int(mask.sum())
            })
    
    return bin_data


# =========================================================================
# Win Rate & Edge
# =========================================================================

def win_rate_confidence_interval(wins: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """
    Wilson score interval for win rate.
    Provides reliable intervals even for small n or extreme win rates.
    """
    if n == 0:
        return (0.0, 1.0)
        
    p = wins / n
    z = stats.norm.ppf((1 + confidence) / 2)
    
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2*n)) / denominator
    margin = (z * math.sqrt(p*(1-p)/n + z**2/(4*n**2))) / denominator
    
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def effective_edge(calculated_edge: float, bid_ask_spread: float, fee: float = 0.02) -> float:
    """
    Estimate edge after execution costs (spread + fees).
    """
    execution_cost = (bid_ask_spread / 2) + fee
    return calculated_edge - execution_cost


# =========================================================================
# Risk & Performance
# =========================================================================

def max_drawdown(equity_curve: list[float]) -> float:
    """
    Calculate the maximum peak-to-trough drawdown as a fraction of current peak.
    Returns a positive number (0.15 = 15% drawdown).
    """
    if not equity_curve:
        return 0.0
        
    # peak = -np.inf
    # max_dd = 0
    # for x in equity_curve:
    #     if x > peak: peak = x
    #     dd = (peak - x) / peak if peak > 0 else 0
    #     if dd > max_dd: max_dd = dd
    # return max_dd
    
    arr = np.array(equity_curve)
    peak = np.maximum.accumulate(arr)
    # Avoid div by zero
    drawdowns = np.where(peak > 0, (peak - arr) / peak, 0)
    return float(np.max(drawdowns))


def sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """
    Annualised Sharpe Ratio for daily returns.
    Assuming 365 trading days for crypto/prediction markets.
    """
    if not returns:
        return 0.0
        
    arr = np.array(returns)
    avg_return = np.mean(arr) - (risk_free_rate / 365)
    std_return = np.std(arr)
    
    if std_return == 0:
        return 0.0
        
    return float((avg_return / std_return) * math.sqrt(365))


def conservative_kelly(
    estimated_edge: float,
    edge_std_error: float,
    fraction: float = 0.25,
    n_concurrent: int = 1
) -> float:
    """
    Kelly sizing adjusted for uncertainty and correlation.
    """
    # Use 90% one-tailed lower bound (z=1.28 or 1.645)
    z = 1.645
    lower_edge = estimated_edge - (z * edge_std_error)
    
    if lower_edge <= 0:
        return 0.0
        
    # Fractional Kelly
    fk = lower_edge * fraction
    
    # Square root adjustment for concurrent positions (simple heuristic)
    if n_concurrent > 1:
        fk = fk / math.sqrt(n_concurrent)
        
    # Hard cap at 5% as per Quant persona
    return min(fk, 0.05)


def aggregate_station_metrics(brier_scores: list[dict]) -> dict[str, dict[str, Any]]:
    """
    Aggregate Brier scores and skill scores per station.
    brier_scores: list of dicts with ['market_id', 'station_id', 'brier', 'baseline_brier']
    """
    stations = {}
    for entry in brier_scores:
        sid = entry.get('station_id', 'unknown')
        if sid not in stations:
            stations[sid] = {'brier_sum': 0.0, 'baseline_sum': 0.0, 'count': 0}
        
        stations[sid]['brier_sum'] += entry['brier']
        stations[sid]['baseline_sum'] += entry.get('baseline_brier', 0.5)
        stations[sid]['count'] += 1
        
    results = {}
    for sid, data in stations.items():
        avg_brier = data['brier_sum'] / data['count']
        avg_baseline = data['baseline_sum'] / data['count']
        results[sid] = {
            'avg_brier': avg_brier,
            'avg_baseline': avg_baseline,
            'bss': brier_skill_score(avg_brier, avg_baseline),
            'count': data['count']
        }
    return results
