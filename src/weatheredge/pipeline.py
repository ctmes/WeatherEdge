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
from weatheredge.polymarket import fetch_weather_markets, parse_bins_from_outcomes, enrich_markets_with_live_prices
from weatheredge.probability import (
    StaleMarketPricesError,
    calculate_edge,
    forecast_to_probabilities,
    kelly_fraction,
    normalise_market_probs,
    recommended_bet_usd,
)
from weatheredge.ws_client import OrderbookSnapshot
from weatheredge.regime import RegimeAssessment, adjust_edge_threshold, assess_regime
from weatheredge.filters import check_midpoint_integrity
from weatheredge.safety import PositionLimits, check_position_limits
from weatheredge.stations import get_station

logger = logging.getLogger("weatheredge.pipeline")


# =========================================================================
# Scan diagnostics
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
    orderbooks: dict[str, OrderbookSnapshot] | None = None,
    deep_scan: bool = False,
) -> tuple[list[EdgeSignal], ScanDiagnostics | None]:
    """
    Execute one full pipeline scan with calibration and regime checks.

    FIX #6: accepts ``orderbooks`` so the standalone scanner can also
    benefit from live orderbook data, not just live_paper.py.
    """
    diag = ScanDiagnostics() if diagnostics else None
    calibrations = calibrations or {}

    logger.info("Starting scan (deep_scan=%s)...", deep_scan)

    markets = await fetch_weather_markets(deep_scan=deep_scan)
    if diag:
        diag.weather_markets_found = len(markets)
    logger.info("Found %d weather markets on Polymarket", len(markets))

    if not markets:
        logger.warning("No weather markets found — nothing to scan.")
        return [], diag

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

    # Warn about unmatched markets — this is operationally significant
    if unmatched_markets:
        sample = ", ".join(
            f"'{m.question[:45]}' (parsed_id={m.station_id!r})"
            for m in unmatched_markets[:3]
        )
        logger.info(
            "%d/%d markets have no matching station and will be skipped. "
            "Sample: %s%s",
            len(unmatched_markets),
            len(markets),
            sample,
            " ..." if len(unmatched_markets) > 3 else "",
        )
    # ── 1b. Enrich with live CLOB midpoint prices ─────────────────────────
    # Updates market.outcome_prices in-place with real-time CLOB data and builds
    # synthetic orderbooks for the midpoint integrity filter.
    if orderbooks is None:
        markets, orderbooks = await enrich_markets_with_live_prices(markets)

    for m in unmatched_markets[:5]:
        logger.debug("Unmatched: %s (station_id=%s)", m.question[:60], m.station_id)

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
                market, fc_by_date, rg_by_date, station_cal, bankroll,
                orderbooks=orderbooks,
            )
            all_signals.extend(signals)

            if diag and market_diag:
                diag.market_details.append(market_diag)
                diag.bins_evaluated += market_diag.get("bins_evaluated", 0)
                if market_diag.get("regime_blocked"):
                    diag.markets_blocked_by_regime += 1

    all_signals.sort(key=lambda s: s.net_edge, reverse=True)
    if diag:
        diag.signals_above_threshold = len(all_signals)

    logger.info("Scan complete — %d signals above threshold", len(all_signals))
    return all_signals, diag


# =========================================================================
# Internals
# =========================================================================

def _resolve_market_price(
    bin_index: int,
    market: ParsedMarket,
    orderbooks: dict[str, Any] | None,
) -> tuple[float, float, float]:
    """
    Resolve the best available market price, integrity multiplier, and
    ask-side depth for a single bin.

    Returns ``(market_price, integrity_multiplier, depth_usd)`` where:
    - ``market_price`` is the mid-price from a live orderbook if available,
      otherwise the Gamma implied probability for this bin.
    - ``integrity_multiplier`` is 1.0 when the book looks healthy, <1.0
      when it is thin or crossed, and 0.5 (not 0.0) when no live book
      exists (FIX #3: 0.0 was blocking all signals with no live data).
    - ``depth_usd`` is the total ask-side depth in USD, used for slippage.
    """
    from weatheredge.filters import check_midpoint_integrity

    # Fallback values when no orderbook is available
    gamma_price = (
        market.outcome_prices[bin_index]
        if bin_index < len(market.outcome_prices)
        else 0.5
    )
    no_book_multiplier = 0.5  # FIX #3: penalise missing book but don't zero out

    if not orderbooks:
        return gamma_price, no_book_multiplier, 0.0

    # Try per-bin token ID first
    tid = market.token_ids[bin_index] if bin_index < len(market.token_ids) else None
    book = orderbooks.get(tid) if tid else None

    # Fallback to condition ID for binary / single-outcome markets
    if book is None and not market.token_ids:
        book = orderbooks.get(market.condition_id)

    if book is None:
        return gamma_price, no_book_multiplier, 0.0

    if isinstance(book, OrderbookSnapshot):
        bids_list = [[lvl.price, lvl.size] for lvl in book.bids]
        asks_list = [[lvl.price, lvl.size] for lvl in book.asks]
        integrity = check_midpoint_integrity(bids_list, asks_list)
        depth_usd = sum(lvl.price * lvl.size for lvl in book.asks)
        return book.mid_price, integrity["multiplier"], depth_usd

    if isinstance(book, dict):
        integrity = check_midpoint_integrity(book.get("bids"), book.get("asks"))
        # Use provided 'mid' or calculate from levels
        mid = book.get("mid")
        if mid is None:
            mid = (
                (book["bids"][0][0] + book["asks"][0][0]) / 2
                if book.get("bids") and book.get("asks")
                else gamma_price
            )
        
        asks = book.get("asks") or []
        depth_usd = sum(a[0] * a[1] for a in asks if len(a) >= 2)
        return mid, integrity["multiplier"], depth_usd

    return gamma_price, no_book_multiplier, 0.0


def _process_market(
    market: ParsedMarket,
    fc_by_date: dict[date, ForecastResult],
    rg_by_date: dict[date, AtmosphericRegime],
    calibration: StationBias | None,
    bankroll: float,
    eval_date: date | None = None,
    current_market_exposure: float = 0.0,
    current_total_exposure: float = 0.0,
    orderbooks: dict[str, Any] | None = None,
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

    # ── Regime check ──────────────────────────────────────────────────
    # Always build a regime object carrying the forecast-derived values.
    # CAPE/wind data from rg_by_date is supplementary; model_divergence and
    # ensemble_spread from ForecastResult are always available and must always
    # be checked — including in backtests where rg_by_date is empty.
    if target_date and target_date in rg_by_date:
        regime = rg_by_date[target_date]
    else:
        from weatheredge.forecast import AtmosphericRegime
        regime = AtmosphericRegime(
            station_id=market.station_id or "",
            valid_date=target_date or forecast.valid_date,
        )

    # Populate regime with model-derived circuit-breaker values.
    regime.model_divergence = forecast.model_divergence
    regime.ensemble_spread = forecast.tmax_std or 0.0

    regime_assessment = assess_regime(
        regime,
        ensemble_spread=forecast.tmax_std,
    )
    detail["regime_flags"] = regime_assessment.flags
    detail["confidence_multiplier"] = regime_assessment.confidence_multiplier
    if forecast.model_divergence > 0:
        detail["model_divergence"] = round(forecast.model_divergence, 2)

    if not regime_assessment.can_trade:
        detail["regime_blocked"] = True
        detail["skip_reason"] = f"regime: {regime_assessment.reason}"
        return [], detail

    # ── Apply calibration ─────────────────────────────────────────────
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

    # ── Model probabilities ───────────────────────────────────────────
    model_probs = forecast_to_probabilities(mean, std, bins)

    # FIX #2: catch StaleMarketPricesError so a market with bad orderbook
    # data doesn't crash the whole cycle — just skip this market.
    try:
        market_probs = normalise_market_probs(market.outcome_prices)
    except StaleMarketPricesError as e:
        logger.warning(
            "Skipping market %s: %s", market.condition_id, e
        )
        detail["skip_reason"] = f"stale market prices: {e}"
        return [], detail

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

    # Identify bins with no raw price data before normalization.
    # A raw price of 0.0 means PMXT enrichment failed — it is NOT a genuine
    # near-zero probability.  Trading these bins creates phantom edge.
    zero_price_bins: set[int] = {
        i for i, p in enumerate(market.outcome_prices) if p < 1e-6
    }
    if zero_price_bins:
        logger.warning(
            "Market %s: %d/%d bins have zero raw prices (PMXT enrichment likely "
            "failed for indices %s) — skipping those bins.",
            market.condition_id,
            len(zero_price_bins),
            len(market.outcome_prices),
            sorted(zero_price_bins),
        )

    # Estimate dynamic slippage based on market volume
    # ── Edge per bin ──────────────────────────────────────────────────
    edge_threshold = adjust_edge_threshold(TRADING.min_edge_threshold, regime_assessment)
    from weatheredge.slippage import estimate_slippage

    signals: list[EdgeSignal] = []

    for i, (mp, mkt_p_gamma) in enumerate(zip(model_probs, market_probs)):
        detail["bins_evaluated"] += 1

        # Skip bins with missing price data (raw zero = no orderbook data)
        if i in zero_price_bins:
            continue

        # Skip the all-inclusive (-inf, +inf) sentinel bin used as the "No" side
        # in YES/NO binary markets.  Its model probability is always ~1.0, which
        # creates a phantom huge edge against the No side.
        if bins[i] == (float("-inf"), float("inf")):
            logger.debug("Skipping all-range bin %d in market %s (YES/NO No-side)", i, market.condition_id)
            continue

        # First pass: estimate edge with a preliminary slippage to check threshold
        # FIX #5: resolve live market price first, then use it consistently
        # throughout the entire bin calculation (preliminary + refined).
        # Previously mkt_p was updated from the orderbook mid-price AFTER
        # the preliminary edge was calculated, causing the threshold check
        # to use a different price than the refined edge calculation.
        mkt_p, integrity_multiplier, ask_depth_usd = _resolve_market_price(
            i, market, orderbooks
        )

        if mkt_p <= 0.001:
            logger.debug("Skipping bin %d: market price too low (%.4f)", i, mkt_p)
            continue

        combined_multiplier = regime_assessment.confidence_multiplier * integrity_multiplier

        # FIX #3: only skip if combined_multiplier is truly zero (i.e. regime
        # has blocked trading), not merely because the orderbook is missing.
        # A missing orderbook now gives integrity_multiplier=0.5, not 0.0.
        if combined_multiplier <= 0:
            logger.debug(
                "Skipping bin %d: combined multiplier is zero (regime blocked)", i
            )
            continue

        # Preliminary screen with rough slippage estimate
        preliminary_slip = estimate_slippage(
            bet_size_usd=bankroll * 0.01,
            market_volume=market.volume,
        )
        edge = calculate_edge(mp, mkt_p, mkt_p, dynamic_slippage=preliminary_slip)

        # Apply regime + integrity multipliers (integrity_multiplier set by _resolve_market_price)
        combined_multiplier = regime_assessment.confidence_multiplier * integrity_multiplier

        # Apply regime confidence multiplier to sizing
        if edge >= edge_threshold and combined_multiplier > 0:
            adjusted_edge = edge * combined_multiplier
            kf = kelly_fraction(adjusted_edge, mkt_p)
            bet = recommended_bet_usd(adjusted_edge, mkt_p, bankroll)

            # Cap bet to max allowed (3% per config/safety)
            if bet > bankroll * 0.03:
                bet = bankroll * 0.03

            # Refine slippage now that we know the actual bet size
            dyn_slippage = estimate_slippage(
                bet_size_usd=bet,
                market_volume=market.volume,
            )

            # Recalculate edge with refined slippage
            refined_edge = calculate_edge(mp, mkt_p, mkt_p, dynamic_slippage=dyn_slippage)
            if refined_edge < edge_threshold:
                # Edge evaporated once we accounted for realistic slippage
                continue

        # FIX #1: pass market_price (mkt_p) to calculate_edge so the updated
        # signature is satisfied. Same for kelly_fraction / recommended_bet_usd.
        preliminary_edge = calculate_edge(
            mp, mkt_p, mkt_p, dynamic_slippage=preliminary_slip
        )
        if preliminary_edge < edge_threshold:
            continue

        # Refine bet size using actual orderbook depth
        rough_bet = min(bankroll * TRADING.kelly_max_bet, bankroll * 0.03)
        dyn_slippage = estimate_slippage(
            bet_size_usd=rough_bet,
            market_volume=market.volume,
            order_book_depth=(
                [(lvl.price, lvl.price * lvl.size) for lvl in orderbooks.get(
                    market.token_ids[i] if i < len(market.token_ids) else "", {}
                ).asks]
                if orderbooks and isinstance(
                    orderbooks.get(market.token_ids[i] if i < len(market.token_ids) else ""),
                    OrderbookSnapshot,
                )
                else None
            ),
        )

        # FIX #1: refined edge also uses correct market_price argument
        refined_edge = calculate_edge(mp, mkt_p, mkt_p, dynamic_slippage=dyn_slippage)
        if refined_edge < edge_threshold:
            continue

        adjusted_edge = refined_edge * combined_multiplier

        # FIX #1: pass mkt_p as market_price so Kelly uses correct payout odds
        kf = kelly_fraction(adjusted_edge, mkt_p)
        bet = recommended_bet_usd(adjusted_edge, mkt_p, bankroll)

        # FIX #4: use config value rather than hardcoded 0.03
        bet_cap = bankroll * TRADING.kelly_max_bet
        if bet > bet_cap:
            bet = round(bet_cap, 2)

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
                raw_edge=mp - (mkt_p + dyn_slippage),
                net_edge=refined_edge,
                kelly_fraction=kf,
                recommended_bet_usd=bet,
                regime_flags=regime_assessment.flags,
                confidence_multiplier=combined_multiplier,
                slippage=dyn_slippage,
            )
        )

    detail["signals_found"] = len(signals)
    return signals, detail