"""
Paper-trade logger — persist every scan's signals, market snapshots,
price snapshots, and forecast data to the database.

Timestamp discipline is critical: every prediction is recorded *before*
resolution so that future validation is unfalsifiable.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from weatheredge.db import get_session
from weatheredge.models import (
    EdgeSignal,
    Forecast,
    Market,
    MarketPrice,
    ParsedMarket,
    Signal,
)
from weatheredge.pipeline import ScanDiagnostics

logger = logging.getLogger("weatheredge.logger")


# =========================================================================
# Public API
# =========================================================================

async def log_scan(
    signals: Sequence[EdgeSignal],
    diagnostics: ScanDiagnostics | None = None,
    scan_time: datetime | None = None,
    markets: dict[str, ParsedMarket] | None = None,
) -> int:
    """
    Persist one scan's results to the database.

    1. Upsert Market rows (keyed on condition_id).
    2. Insert MarketPrice snapshots.
    3. Insert Signal rows for every edge signal.

    Returns the number of Signal rows written.
    """
    scan_time = scan_time or datetime.utcnow()

    async with get_session() as session:
        written = 0

        # Group signals by market for efficient upsert
        market_signals: dict[str, list[EdgeSignal]] = {}
        for sig in signals:
            market_signals.setdefault(sig.market_id, []).append(sig)

        for market_id, sigs in market_signals.items():
            # ── Upsert Market ──────────────────────────────────────
            market_data = markets.get(market_id) if markets else None
            await _upsert_market(session, sigs[0], scan_time, market_data=market_data)

            # ── Insert Signals ─────────────────────────────────────
            for sig in sigs:
                db_signal = Signal(
                    market_id=sig.market_id,
                    bin_index=sig.bin_index,
                    model_prob=sig.model_prob,
                    market_prob=sig.market_prob,
                    edge=sig.net_edge,
                    kelly_fraction=sig.kelly_fraction,
                    bet_size_usd=sig.recommended_bet_usd,
                    regime_flags=sig.regime_flags,
                    confidence_multiplier=sig.confidence_multiplier,
                    signal_type="paper",
                    created_at=scan_time,
                )
                session.add(db_signal)
                written += 1

        # Flush so IDs are assigned before commit
        await session.flush()

        logger.info(
            "Logged %d signals across %d markets (scan_time=%s)",
            written, len(market_signals), scan_time.isoformat(),
        )
        return written


async def log_market_prices(
    market_id: str,
    prices: dict[int, float],
    recorded_at: datetime | None = None,
) -> None:
    """Insert a MarketPrice snapshot for a single market."""
    recorded_at = recorded_at or datetime.utcnow()
    async with get_session() as session:
        session.add(MarketPrice(
            market_id=market_id,
            prices=prices,
            recorded_at=recorded_at,
        ))


async def log_forecast(
    station_id: str,
    model: str,
    valid_date,
    tmax_mean: float,
    tmax_std: float,
    tmin_mean: float | None = None,
    tmin_std: float | None = None,
    precip_mean: float | None = None,
    run_time: datetime | None = None,
) -> None:
    """Insert a Forecast snapshot."""
    run_time = run_time or datetime.utcnow()
    async with get_session() as session:
        session.add(Forecast(
            station_id=station_id,
            model=model,
            run_time=run_time,
            valid_date=valid_date,
            tmax_mean=tmax_mean,
            tmax_std=tmax_std,
            tmin_mean=tmin_mean,
            tmin_std=tmin_std,
            precip_mean=precip_mean,
        ))


# =========================================================================
# Internals
# =========================================================================

async def _upsert_market(
    session: AsyncSession,
    sig: EdgeSignal,
    scan_time: datetime,
    market_data: ParsedMarket | None = None,
) -> None:
    """Create or update a Market row from an EdgeSignal + optional ParsedMarket."""
    from datetime import datetime as _dt

    result = await session.execute(
        select(Market).where(Market.id == sig.market_id)
    )
    existing = result.scalar_one_or_none()

    # Parse end_date string → datetime
    end_date_dt = None
    if market_data and market_data.end_date:
        try:
            end_date_dt = _dt.fromisoformat(
                market_data.end_date.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (ValueError, TypeError):
            pass

    if existing is None:
        market = Market(
            id=sig.market_id,
            question=sig.question,
            station_id=sig.station_id,
            created_at=scan_time,
        )
        if market_data:
            market.metric = market_data.metric
            market.bins = [list(b) for b in market_data.bins] if market_data.bins else None
            market.outcomes = market_data.outcomes
            market.outcome_prices = market_data.outcome_prices
            market.token_ids = market_data.token_ids
            market.volume = market_data.volume
            market.description = market_data.description
            market.timezone = market_data.timezone
            market.resolution_date = market_data.resolution_date
            market.end_date = end_date_dt
        session.add(market)
    else:
        # Update metadata fields that may have been missing on first insert
        if market_data:
            if existing.metric is None and market_data.metric:
                existing.metric = market_data.metric
            if existing.bins is None and market_data.bins:
                existing.bins = [list(b) for b in market_data.bins]
            if existing.outcomes is None and market_data.outcomes:
                existing.outcomes = market_data.outcomes
            if existing.outcome_prices is None and market_data.outcome_prices:
                existing.outcome_prices = market_data.outcome_prices
            if existing.resolution_date is None and market_data.resolution_date:
                existing.resolution_date = market_data.resolution_date
            if existing.end_date is None and end_date_dt:
                existing.end_date = end_date_dt
