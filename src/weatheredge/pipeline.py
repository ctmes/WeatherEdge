"""
Main pipeline orchestrator — ties Polymarket scraper, Open-Meteo forecasts,
calibration engine, regime flags, and safety guards together.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from weatheredge.calibration import StationBias, apply_calibration, apply_calibration_std
from weatheredge.config import TRADING
from weatheredge.forecast import AtmosphericRegime, fetch_forecast, fetch_regime_data
from weatheredge.models import EdgeSignal, ForecastResult, ParsedMarket
from weatheredge.polymarket import fetch_weather_markets, parse_bins_from_outcomes
from weatheredge.probability import (
    calculate_edge,
    forecast_to_probabilities,
    kelly_fraction,
    normalise_market_probs,
    recommended_bet_usd,
)
from weatheredge.regime import RegimeAssessment, adjust_edge_threshold, assess_regime
from weatheredge.safety import PositionLimits, check_position_limits
from weatheredge.stations import get_station

logger = logging.getLogger("weatheredge.pipeline")


# =========================================================================
# Scan diagnostics — so we can see what's happening
# =========================================================================

@dataclass
class ScanDiagnostics:
    """Detailed diagnostics for a pipeline scan run."""

    timestamp: datetime = field(default_factory=datetime.utcnow)
    total_markets_fetched: int = 0
    weather_markets_found: int = 0
    markets_with_station: int = 0
    markets_without_station: int = 0
    stations_matched: int = 0
    forecasts_fetched: int = 0
    markets_processed: int = 0
    markets_blocked_by_regime: int = 0
    bins_evaluated: int = 0
    signals_above_threshold: int = 0
    signals_blocked_by_limits: int = 0

    # Per-market details
    market_details: list[dict[str, Any]] = field(default_factory=list)
    regime_flags: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# =========================================================================
# Public API
# =========================================================================

async def run_scan(
    bankroll: float = 10_000.0,
    calibrations: dict[str, StationBias] | None = None,
    diagnostics: bool = False,
) -> tuple[list[EdgeSignal], ScanDiagnostics | None]:
    """
    Execute one full pipeline scan with calibration and regime checks:
      1. Fetch active weather markets from Polymarket
      2. Parse resolution rules → extract station IDs
      3. Look up station coordinates
      4. Fetch ensemble forecasts + regime data from Open-Meteo
      5. Apply station calibration corrections
      6. Check atmospheric regime flags
      7. Convert calibrated forecasts to bin probabilities
      8. Compare against market implied probabilities
      9. Calculate edge per bin with safety checks
     10. Return signals above threshold

    Returns (signals, diagnostics) — diagnostics is None if diagnostics=False.
    """
    diag = ScanDiagnostics() if diagnostics else None
    calibrations = calibrations or {}

    logger.info("Starting scan...")

    # ── 1. Fetch markets ──────────────────────────────────────────────
    markets = await fetch_weather_markets()
    if diag:
        diag.weather_markets_found = len(markets)
    logger.info("Found %d weather markets on Polymarket", len(markets))

    if not markets:
        logger.warning("No weather markets found — nothing to scan.")
        return [], diag

    # ── 2–3. Match to stations ────────────────────────────────────────
    station_markets: dict[str, list[ParsedMarket]] = {}
    unmatched_markets: list[ParsedMarket] = []

    for m in markets:
        if not m.station_id:
            unmatched_markets.append(m)
            continue
        station = get_station(m.station_id)
        if station is None:
            unmatched_markets.append(m)
            continue
        station_markets.setdefault(m.station_id, []).append(m)

    matched_count = sum(len(v) for v in station_markets.values())
    if diag:
        diag.markets_with_station = matched_count
        diag.markets_without_station = len(unmatched_markets)
        diag.stations_matched = len(station_markets)

    logger.info(
        "Matched %d markets to %d stations (%d unmatched)",
        matched_count, len(station_markets), len(unmatched_markets),
    )

    # Log unmatched for debugging
    for m in unmatched_markets[:5]:
        logger.debug("Unmatched: %s (station_id=%s)", m.question[:60], m.station_id)

    # ── 4. Fetch forecasts + regime data ──────────────────────────────
    forecasts: dict[str, list[ForecastResult]] = {}
    regime_data: dict[str, list[AtmosphericRegime]] = {}

    async def _fetch_all(sid: str) -> None:
        station = get_station(sid)
        if station is None:
            return
        try:
            fc_results, rg_results = await asyncio.gather(
                fetch_forecast(
                    latitude=station.latitude,
                    longitude=station.longitude,
                    timezone=station.timezone,
                ),
                fetch_regime_data(
                    latitude=station.latitude,
                    longitude=station.longitude,
                    timezone=station.timezone,
                ),
            )
            for r in fc_results:
                r.station_id = sid
            for r in rg_results:
                r.station_id = sid
            forecasts[sid] = fc_results
            regime_data[sid] = rg_results
        except Exception as e:
            logger.warning("Failed to fetch data for %s: %s", sid, e)
            if diag:
                diag.errors.append(f"Fetch failed for {sid}: {e}")

    await asyncio.gather(*[_fetch_all(sid) for sid in station_markets])
    if diag:
        diag.forecasts_fetched = len(forecasts)
    logger.info("Fetched forecasts for %d stations", len(forecasts))

    # ── 5–9. Calculate edges with calibration + regime checks ─────────
    all_signals: list[EdgeSignal] = []

    for station_id, market_list in station_markets.items():
        station_forecasts = forecasts.get(station_id, [])
        station_regimes = regime_data.get(station_id, [])
        station_cal = calibrations.get(station_id)

        if not station_forecasts:
            continue

        fc_by_date: dict[date, ForecastResult] = {
            f.valid_date: f for f in station_forecasts
        }
        rg_by_date: dict[date, AtmosphericRegime] = {
            r.valid_date: r for r in station_regimes
        }

        for market in market_list:
            if diag:
                diag.markets_processed += 1

            signals, market_diag = _process_market(
                market, fc_by_date, rg_by_date, station_cal, bankroll
            )
            all_signals.extend(signals)

            if diag and market_diag:
                diag.market_details.append(market_diag)
                diag.bins_evaluated += market_diag.get("bins_evaluated", 0)
                if market_diag.get("regime_blocked"):
                    diag.markets_blocked_by_regime += 1

    # ── 10. Sort by edge ──────────────────────────────────────────────
    all_signals.sort(key=lambda s: s.net_edge, reverse=True)
    if diag:
        diag.signals_above_threshold = len(all_signals)

    logger.info(
        "Scan complete — %d signals above threshold",
        len(all_signals),
    )

    return all_signals, diag


# =========================================================================
# Internals
# =========================================================================

def _process_market(
    market: ParsedMarket,
    fc_by_date: dict[date, ForecastResult],
    rg_by_date: dict[date, AtmosphericRegime],
    calibration: StationBias | None,
    bankroll: float,
    eval_date: date | None = None,
    current_market_exposure: float = 0.0,
    current_total_exposure: float = 0.0,
) -> tuple[list[EdgeSignal], dict[str, Any] | None]:
    """Generate EdgeSignals for one market, applying calibration + regime checks."""

    detail: dict[str, Any] = {
        "question": market.question[:80],
        "station_id": market.station_id,
        "outcomes": market.outcomes,
        "market_prices": market.outcome_prices,
        "bins_evaluated": 0,
        "regime_blocked": False,
        "signals_found": 0,
    }

    # Parse bins from outcomes
    bins = parse_bins_from_outcomes(market.outcomes) if not market.bins else market.bins
    if not bins:
        detail["skip_reason"] = "no parseable bins"
        return [], detail

    # Find matching forecast
    forecast: ForecastResult | None = None
    target_date: date | None = None
    if market.resolution_date:
        forecast = fc_by_date.get(market.resolution_date)
        target_date = market.resolution_date

    if forecast is None and fc_by_date:
        today = eval_date or date.today()
        # In backtesting, sometimes we only have one date in the dict and it's exact
        # If eval_date is passed, we can just use the exact match if available
        if eval_date and eval_date in fc_by_date:
            target_date = eval_date
            forecast = fc_by_date[eval_date]
        else:
            future = sorted([d for d in fc_by_date if d >= today])
            if future:
                target_date = future[0]
                forecast = fc_by_date[target_date]

    if forecast is None:
        detail["skip_reason"] = "no matching forecast"
        return [], detail

    # ── Regime check ──────────────────────────────────────────────
    regime_assessment = RegimeAssessment(station_id=market.station_id or "")
    if target_date and target_date in rg_by_date:
        regime = rg_by_date[target_date]
        regime_assessment = assess_regime(regime)
        detail["regime_flags"] = regime_assessment.flags
        detail["confidence_multiplier"] = regime_assessment.confidence_multiplier

    if not regime_assessment.can_trade:
        detail["regime_blocked"] = True
        detail["skip_reason"] = f"regime: {regime_assessment.reason}"
        return [], detail

    # ── Apply calibration ─────────────────────────────────────────
    if market.metric == "tmin" and forecast.tmin_mean is not None:
        raw_mean = forecast.tmin_mean
        raw_std = forecast.tmin_std
    else:
        raw_mean = forecast.tmax_mean
        raw_std = forecast.tmax_std

    if raw_std is None:
        detail["skip_reason"] = "missing standard deviation"
        return [], detail

    if calibration and target_date:
        mean = apply_calibration(
            raw_mean, calibration, target_date,
            metric=market.metric or "tmax",
        )
        std = apply_calibration_std(raw_std, calibration)
        detail["calibration_applied"] = True
        detail["raw_mean"] = raw_mean
        detail["calibrated_mean"] = mean
    else:
        mean = raw_mean
        std = raw_std
        detail["calibration_applied"] = False

    # ── Model probabilities ───────────────────────────────────────
    model_probs = forecast_to_probabilities(mean, std, bins)
    market_probs = normalise_market_probs(market.outcome_prices)

    detail["model_probs"] = [round(p, 4) for p in model_probs]
    detail["market_probs"] = [round(p, 4) for p in market_probs]

    if len(model_probs) != len(market_probs):
        detail["skip_reason"] = "prob length mismatch"
        return [], detail

    # ── Edge per bin ──────────────────────────────────────────────
    # Adjust threshold based on regime
    edge_threshold = adjust_edge_threshold(
        TRADING.min_edge_threshold, regime_assessment
    )

    signals: list[EdgeSignal] = []
    for i, (mp, mkt_p) in enumerate(zip(model_probs, market_probs)):
        detail["bins_evaluated"] += 1
        edge = calculate_edge(mp, mkt_p)

        # Apply regime confidence multiplier to sizing
        if edge >= edge_threshold:
            adjusted_edge = edge * regime_assessment.confidence_multiplier
            kf = kelly_fraction(adjusted_edge)
            bet = recommended_bet_usd(adjusted_edge, bankroll)

            # Cap bet to max allowed (3% per config/safety)
            if bet > bankroll * 0.03:
                bet = bankroll * 0.03

            # Position limit check
            allowed, reason = check_position_limits(
                proposed_bet_usd=bet,
                bankroll=bankroll,
                current_market_exposure=current_market_exposure,
                current_total_exposure=current_total_exposure,
            )
            if not allowed:
                logger.debug("Position limit blocked: %s", reason)
                continue

            label = market.outcomes[i] if i < len(market.outcomes) else f"Bin {i}"
            signals.append(
                EdgeSignal(
                    market_id=market.condition_id,
                    question=market.question,
                    station_id=market.station_id or "???",
                    bin_index=i,
                    bin_label=label,
                    bin_range=bins[i],
                    model_prob=mp,
                    market_prob=mkt_p,
                    raw_edge=mp - (mkt_p + TRADING.assumed_slippage),
                    net_edge=edge,
                    kelly_fraction=kf,
                    recommended_bet_usd=bet,
                    slippage=TRADING.assumed_slippage,
                )
            )

    detail["signals_found"] = len(signals)
    return signals, detail
