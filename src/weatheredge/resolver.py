"""
Resolution tracker — check whether paper-traded markets have resolved,
score predictions, and write Result + BrierScore rows.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, func

from weatheredge.cache import cache_key as _cache_key, read_cache, write_cache
from weatheredge.config import POLYMARKET_GAMMA_BASE
from weatheredge.db import get_session, get_next_market_price
from weatheredge.models import BrierScore, Market, Result, Signal, StationCalibration
from weatheredge.probability import brier_score

logger = logging.getLogger("weatheredge.resolver")
_SUBDIR = "resolver_cache"


# =========================================================================
# Public API
# =========================================================================

async def check_resolutions() -> int:
    """
    Find unresolved signals whose markets should have resolved,
    fetch outcomes, and write Result + BrierScore rows.

    Returns the number of signals resolved this run.
    """
    resolved_count = 0

    async with get_session() as session:
        # Find signals without a result whose market resolution date is past
        stmt = (
            select(Signal)
            .outerjoin(Result, Result.signal_id == Signal.id)
            .where(Result.id.is_(None))  # no result yet
            .order_by(Signal.created_at)
        )
        result = await session.execute(stmt)
        unresolved_signals = result.scalars().all()

        if not unresolved_signals:
            logger.debug("No unresolved signals found.")
            return 0

        logger.info("Found %d unresolved signals.", len(unresolved_signals))

        # Group by market_id
        signals_by_market: dict[str, list[Signal]] = {}
        for sig in unresolved_signals:
            signals_by_market.setdefault(sig.market_id, []).append(sig)

        # Try to resolve each market
        for market_id, sigs in signals_by_market.items():
            try:
                outcome = await _fetch_resolved_outcome(market_id)
            except Exception:
                logger.warning(
                    "Failed to fetch resolution status for market %s — will retry next cycle.",
                    market_id,
                    exc_info=True,
                )
                continue

            if outcome is None:
                # Polymarket hasn't closed this market — try observation fallback
                # for markets whose end_date is more than 6 hours in the past.
                # IEM daily observations are typically finalized within a few hours
                # of midnight, so 6h is safe without waiting 48h for Polymarket.
                market_row = await session.get(Market, market_id)
                if market_row is not None:
                    # Use end_date if available, else fall back to resolution_date
                    cutoff_dt = market_row.end_date
                    if cutoff_dt is None and market_row.resolution_date is not None:
                        res = market_row.resolution_date
                        cutoff_dt = datetime(res.year, res.month, res.day, 23, 59, 59)
                if market_row is not None and cutoff_dt is not None:
                    age = datetime.utcnow() - cutoff_dt
                    if age > timedelta(hours=6):
                        try:
                            outcome = await _fetch_observed_outcome(session, market_row)
                            if outcome:
                                logger.info(
                                    "Resolved market %s via observation fallback (bin %d).",
                                    market_id, outcome["resolved_bin"],
                                )
                        except Exception:
                            logger.warning(
                                "Observation fallback failed for market %s.",
                                market_id,
                                exc_info=True,
                            )

            if outcome is None:
                continue

            resolved_bin = outcome["resolved_bin"]
            resolve_time = datetime.utcnow()

            for sig in sigs:
                # Try to find the "actual" market price shortly after the signal
                # to simulate slippage/execution for paper trades.
                next_prices = await get_next_market_price(session, sig.market_id, sig.created_at)
                
                actual_fill = None
                actual_slip = None
                
                if next_prices and sig.bin_index in next_prices:
                    # For paper trades, we'll assume we got filled at the next available MIDPOINT
                    # plus a small penalty if we want to be conservative, 
                    # but for now, the next midpoint is our "actual_fill_price".
                    actual_fill = next_prices[sig.bin_index]
                    # slippage = fill - intended (market_prob at signal time)
                    actual_slip = actual_fill - (sig.market_prob or actual_fill)
                    
                    if abs(actual_slip) > 0.001:
                        logger.info("Slippage detected on %s: Est %.4f vs Act %.4f", 
                                    sig.market_id, sig.slippage or 0, actual_slip)

                won = sig.bin_index == resolved_bin
                pnl = _calculate_paper_pnl(sig, won, actual_fill=actual_fill)

                db_result = Result(
                    signal_id=sig.id,
                    resolved_bin=resolved_bin,
                    won=won,
                    pnl_usd=pnl,
                    actual_fill_price=actual_fill,
                    actual_slippage=actual_slip,
                    resolved_at=resolve_time,
                )
                session.add(db_result)
                resolved_count += 1

            # Brier score for this market
            await _write_brier_score(session, market_id, sigs, resolved_bin)

        await session.flush()

    logger.info("Resolved %d signals this run.", resolved_count)
    return resolved_count


# =========================================================================
# Fetching resolution data
# =========================================================================

async def _fetch_resolved_outcome(market_id: str) -> dict[str, Any] | None:
    """
    Check the Polymarket Gamma API for a market's resolution status.

    Returns {"resolved_bin": int} if the market is resolved, else None.
    """
    ck = _cache_key("resolved", market_id)
    cached = read_cache(_SUBDIR, ck)  # Permanent cache for resolutions
    if cached is not None:
        return cached

    url = f"{POLYMARKET_GAMMA_BASE}/markets/{market_id}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
        if resp.status_code == 404:
            logger.debug("Market %s not found on Polymarket (404) — will try observation fallback.", market_id)
            return None
        resp.raise_for_status()
        data = resp.json()

    # Check for resolved status
    if not data.get("closed", False):
        return None

    # Find the winning outcome — the one whose price resolved to ~1.0
    outcome_prices = data.get("outcomePrices", "")
    if isinstance(outcome_prices, str):
        try:
            import json
            prices = json.loads(outcome_prices)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(outcome_prices, list):
        prices = outcome_prices
    else:
        return None

    # The resolved outcome has price close to 1.0
    resolved_bin = None
    for i, price in enumerate(prices):
        p = float(price) if isinstance(price, str) else price
        if p > 0.90:
            resolved_bin = i
            break

    if resolved_bin is None:
        logger.debug("Market %s is closed but no clear winning outcome.", market_id)
        return None

    result = {"resolved_bin": resolved_bin}
    write_cache(_SUBDIR, ck, result)
    return result


# =========================================================================
# Observation-based fallback resolution
# =========================================================================

async def _fetch_observed_outcome(session, market: Market) -> dict[str, Any] | None:
    """
    Resolve a market by fetching the actual weather observation from IEM/ERA5
    and finding which bin the observed value falls into.

    Used when Polymarket hasn't marked the market as closed after 48h.
    """
    from weatheredge.mesonet import fetch_daily_observations

    if not market.station_id or not market.metric or not market.bins or not market.resolution_date:
        logger.debug("Market %s missing required fields for observation fallback.", market.id)
        return None

    res_date = market.resolution_date
    if isinstance(res_date, str):
        res_date = date.fromisoformat(res_date)

    # Look up lat/lon for ERA5 fallback
    cal = await session.get(StationCalibration, market.station_id)
    lat = cal.latitude if cal else None
    lon = cal.longitude if cal else None

    obs_list = await fetch_daily_observations(
        market.station_id, res_date, res_date, latitude=lat, longitude=lon
    )

    if not obs_list:
        logger.debug("No observation data for %s on %s.", market.station_id, res_date)
        return None

    obs = obs_list[0]
    metric = market.metric.lower()
    if metric in ("tmax", "temperature_max"):
        observed_val = obs.tmax_c
    elif metric in ("tmin", "temperature_min"):
        observed_val = obs.tmin_c
    elif metric in ("precip", "precipitation"):
        observed_val = obs.precip_mm
    else:
        logger.debug("Unknown metric %r for market %s.", market.metric, market.id)
        return None

    if observed_val is None:
        logger.debug("Observation value is None for %s %s on %s.", market.station_id, metric, res_date)
        return None

    bins = market.bins  # [{low, high}, ...] or [[low, high], ...]
    resolved_bin = None
    for i, b in enumerate(bins):
        if isinstance(b, dict):
            low = b.get("low")
            high = b.get("high")
        else:
            # list format [low, high]
            low = b[0] if len(b) > 0 else None
            high = b[1] if len(b) > 1 else None
        # Handle unbounded edges
        below = (low is None or observed_val >= low)
        above = (high is None or observed_val < high)
        if below and above:
            resolved_bin = i
            break

    if resolved_bin is None:
        # Clamp to last bin if value exceeds all ranges
        resolved_bin = len(bins) - 1
        logger.debug(
            "Value %.2f outside all bins for market %s — clamping to bin %d.",
            observed_val, market.id, resolved_bin,
        )

    logger.info(
        "Observation fallback: %s %s=%.2f on %s → bin %d",
        market.station_id, metric, observed_val, res_date, resolved_bin,
    )
    return {"resolved_bin": resolved_bin}


# =========================================================================
# Scoring
# =========================================================================

def _calculate_paper_pnl(signal: Signal, won: bool, actual_fill: float | None = None) -> float:
    """
    Calculate paper P&L for a signal, incorporating actual slippage if available.

    Paper bet $X at market_prob price:
      - Win: gross_payout * (1 - 0.02) * (1 - 0.02) - bet  (~3.96% total fee drag)
      - Lose: -$X

    Two-sided fee: Polymarket charges ~2% on entry and ~2% on exit/resolution.
    """
    bet = signal.bet_size_usd or 0.0
    if bet <= 0:
        return 0.0

    if won:
        # Use actual_fill if we have it, else fallback to market_prob (0 slippage)
        fill_price = actual_fill if actual_fill is not None else (signal.market_prob or 0.5)

        if fill_price <= 0:
            fill_price = 0.01

        gross_payout = bet * (1.0 / fill_price)
        net_payout = gross_payout * (1 - 0.02) * (1 - 0.02)
        return net_payout - bet
    else:
        return -bet


async def _write_brier_score(
    session,
    market_id: str,
    signals: list[Signal],
    resolved_bin: int,
) -> None:
    """Compute and store a Brier score for this market resolution."""
    if not signals:
        return

    # Collect model probabilities across all bins from our signals
    # Build a full probability vector: default to 0 for bins we didn't signal on
    max_bin = max(s.bin_index for s in signals) + 1
    max_bin = max(max_bin, resolved_bin + 1)

    model_probs = [0.0] * max_bin
    for sig in signals:
        if sig.bin_index < max_bin:
            model_probs[sig.bin_index] = sig.model_prob or 0.0

    # Normalise if we don't have all bins (best effort)
    total = sum(model_probs)
    if total > 0:
        model_probs = [p / total for p in model_probs]

    score = brier_score(model_probs, resolved_bin)

    session.add(BrierScore(
        market_id=market_id,
        model="weatheredge",
        brier_score=score,
    ))
    logger.debug("Brier score for %s: %.4f", market_id, score)
