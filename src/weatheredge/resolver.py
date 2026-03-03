"""
Resolution tracker — check whether paper-traded markets have resolved,
score predictions, and write Result + BrierScore rows.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import select, func

from weatheredge.config import POLYMARKET_GAMMA_BASE
from weatheredge.db import get_session
from weatheredge.models import BrierScore, Market, Result, Signal
from weatheredge.probability import brier_score

logger = logging.getLogger("weatheredge.resolver")


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
                logger.debug("Market %s not yet resolved or fetch failed.", market_id)
                continue

            if outcome is None:
                continue

            resolved_bin = outcome["resolved_bin"]
            resolve_time = datetime.utcnow()

            for sig in sigs:
                won = sig.bin_index == resolved_bin
                pnl = _calculate_paper_pnl(sig, won)

                db_result = Result(
                    signal_id=sig.id,
                    resolved_bin=resolved_bin,
                    won=won,
                    pnl_usd=pnl,
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
    url = f"{POLYMARKET_GAMMA_BASE}/markets/{market_id}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
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

    return {"resolved_bin": resolved_bin}


# =========================================================================
# Scoring
# =========================================================================

def _calculate_paper_pnl(signal: Signal, won: bool) -> float:
    """
    Calculate paper P&L for a signal.

    Paper bet $X at market_prob price:
      - Win: $X * (1 / market_prob - 1) minus 2% fee
      - Lose: -$X
    """
    bet = signal.bet_size_usd or 0.0
    if bet <= 0:
        return 0.0

    if won:
        market_prob = signal.market_prob or 0.5
        if market_prob <= 0:
            market_prob = 0.01
        gross_return = bet * (1.0 / market_prob - 1.0)
        fee = gross_return * 0.02  # 2% fee on winnings
        return gross_return - fee
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
