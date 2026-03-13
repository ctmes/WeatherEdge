"""
Async SQLAlchemy database layer — engine, session factory, and DDL bootstrap.

Uses the DATABASE_URL from config (defaults to local SQLite file).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from weatheredge.config import DATABASE_URL
from weatheredge.models import Base

logger = logging.getLogger("weatheredge.db")

# ---------------------------------------------------------------------------
# Singleton engine
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the singleton async engine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(DATABASE_URL, echo=False)
        logger.info("Created async engine: %s", DATABASE_URL.split("///")[0] + "///...")
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager yielding an ``AsyncSession``."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# DDL bootstrap
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create all tables defined in ``models.Base.metadata`` and apply missing columns."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _migrate_sync(connection):
            from sqlalchemy import text

            # signals table
            cursor = connection.execute(text("PRAGMA table_info(signals)"))
            signals_cols = [row[1] for row in cursor.fetchall()]

            if "confidence_multiplier" not in signals_cols:
                logger.info("Auto-migrating: adding 'confidence_multiplier' to 'signals'")
                connection.execute(text(
                    "ALTER TABLE signals ADD COLUMN confidence_multiplier FLOAT DEFAULT 1.0"
                ))

            if "regime_flags" not in signals_cols:
                logger.info("Auto-migrating: adding 'regime_flags' to 'signals'")
                connection.execute(text(
                    "ALTER TABLE signals ADD COLUMN regime_flags JSON"
                ))

            # results table
            cursor = connection.execute(text("PRAGMA table_info(results)"))
            results_cols = [row[1] for row in cursor.fetchall()]

            if "actual_fill_price" not in results_cols:
                logger.info("Auto-migrating: adding 'actual_fill_price' to 'results'")
                connection.execute(text(
                    "ALTER TABLE results ADD COLUMN actual_fill_price FLOAT"
                ))

            if "actual_slippage" not in results_cols:
                logger.info("Auto-migrating: adding 'actual_slippage' to 'results'")
                connection.execute(text(
                    "ALTER TABLE results ADD COLUMN actual_slippage FLOAT"
                ))

            # brier_scores table
            cursor = connection.execute(text("PRAGMA table_info(brier_scores)"))
            brier_scores_cols = [row[1] for row in cursor.fetchall()]

            if "baseline_brier" not in brier_scores_cols:
                logger.info("Auto-migrating: adding 'baseline_brier' to 'brier_scores'")
                connection.execute(text(
                    "ALTER TABLE brier_scores ADD COLUMN baseline_brier FLOAT"
                ))

            # station_calibration table
            cursor = connection.execute(text("PRAGMA table_info(station_calibration)"))
            cal_cols = [row[1] for row in cursor.fetchall()]

            for col, typedef in [
                ("tmax_rmse",     "FLOAT DEFAULT 0"),
                ("tmin_rmse",     "FLOAT DEFAULT 0"),
                ("tmax_p_values", "JSON"),
                ("tmin_p_values", "JSON"),
            ]:
                if col not in cal_cols:
                    logger.info("Auto-migrating: adding '%s' to 'station_calibration'", col)
                    connection.execute(text(
                        f"ALTER TABLE station_calibration ADD COLUMN {col} {typedef}"
                    ))

        await conn.run_sync(_migrate_sync)

    logger.info("Database tables initialised and migrated.")


# ---------------------------------------------------------------------------
# Open position tracking
# ---------------------------------------------------------------------------

async def get_open_signal_keys(session: AsyncSession) -> set[tuple[str, int]]:
    """
    Return the set of ``(market_id, bin_index)`` pairs for all signals that
    have been placed but not yet resolved.

    An open position is a ``Signal`` row of type ``'paper'`` or ``'real'``
    that has no corresponding ``Result`` row (i.e. the market has not
    resolved yet).

    This is used by the execution module to prevent re-firing the same
    signal every cycle across process restarts.  The in-memory
    ``_executed_signals`` set is seeded from this on startup, so positions
    survive VPS reboots and redeployments.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import outerjoin
    from weatheredge.models import Result, Signal

    stmt = (
        select(Signal.market_id, Signal.bin_index)
        .outerjoin(Result, Result.signal_id == Signal.id)
        .where(Signal.signal_type.in_(["paper", "real"]))
        .where(Result.id.is_(None))  # no result row = position still open
    )
    rows = await session.execute(stmt)
    return {(row.market_id, row.bin_index) for row in rows}


async def has_open_position(
    session: AsyncSession,
    market_id: str,
    bin_index: int,
) -> bool:
    """
    Return True if an open (unresolved) signal already exists for this
    ``(market_id, bin_index)`` pair.

    Cheaper than loading the full open-position set when checking a single
    signal at execution time.
    """
    from sqlalchemy import select
    from weatheredge.models import Result, Signal

    stmt = (
        select(Signal.id)
        .outerjoin(Result, Result.signal_id == Signal.id)
        .where(Signal.market_id == market_id)
        .where(Signal.bin_index == bin_index)
        .where(Signal.signal_type.in_(["paper", "real"]))
        .where(Result.id.is_(None))
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Market helpers
# ---------------------------------------------------------------------------

async def upsert_market(session: AsyncSession, market_data: dict) -> None:
    """
    Upsert a Market row using on_conflict_do_update (idempotent).

    ``market_data`` must include ``id`` (primary key) plus any columns
    to insert/update.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from weatheredge.models import Market

    stmt = sqlite_insert(Market).values(**market_data)
    update_cols = {
        k: v for k, v in market_data.items()
        if k not in ("id", "created_at")
    }
    if update_cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=[Market.id],
            set_=update_cols,
        )
    else:
        stmt = stmt.on_conflict_do_nothing()
    await session.execute(stmt)


# ---------------------------------------------------------------------------
# Calibration helpers
# ---------------------------------------------------------------------------

async def upsert_calibration(session: AsyncSession, bias: Any) -> None:
    """
    Upsert a StationCalibration row.
    `bias` should be a StationBias object from calibration.py.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from weatheredge.models import StationCalibration

    data = {
        "station_id": bias.station_id,
        "tmax_bias_summer": bias.tmax_bias_summer,
        "tmax_bias_winter": bias.tmax_bias_winter,
        "tmax_bias_spring": bias.tmax_bias_spring,
        "tmax_bias_autumn": bias.tmax_bias_autumn,
        "tmin_bias_summer": bias.tmin_bias_summer,
        "tmin_bias_winter": bias.tmin_bias_winter,
        "tmin_bias_spring": bias.tmin_bias_spring,
        "tmin_bias_autumn": bias.tmin_bias_autumn,
        "tmax_rmse": bias.tmax_rmse,
        "tmin_rmse": bias.tmin_rmse,
        "tmax_p_values": bias.tmax_p_values,
        "tmin_p_values": bias.tmin_p_values,
        "lead_time_degradation": bias.lead_time_degradation,
        "last_updated": datetime.utcnow(),
    }

    stmt = sqlite_insert(StationCalibration).values(**data)
    update_cols = {k: v for k, v in data.items() if k != "station_id"}
    stmt = stmt.on_conflict_do_update(
        index_elements=[StationCalibration.station_id],
        set_=update_cols,
    )
    await session.execute(stmt)


async def get_calibrations(session: AsyncSession) -> dict[str, Any]:
    """Fetch all stored calibrations as a dict mapping station_id -> StationBias."""
    from sqlalchemy import select
    from weatheredge.calibration import StationBias
    from weatheredge.models import StationCalibration

    result = await session.execute(select(StationCalibration))
    rows = result.scalars().all()

    calibrations = {}
    for r in rows:
        bias = StationBias(
            station_id=r.station_id,
            tmax_bias_summer=r.tmax_bias_summer or 0.0,
            tmax_bias_winter=r.tmax_bias_winter or 0.0,
            tmax_bias_spring=r.tmax_bias_spring or 0.0,
            tmax_bias_autumn=r.tmax_bias_autumn or 0.0,
            tmin_bias_summer=r.tmin_bias_summer or 0.0,
            tmin_bias_winter=r.tmin_bias_winter or 0.0,
            tmin_bias_spring=r.tmin_bias_spring or 0.0,
            tmin_bias_autumn=r.tmin_bias_autumn or 0.0,
            tmax_rmse=r.tmax_rmse or 0.0,
            tmin_rmse=r.tmin_rmse or 0.0,
            tmax_p_values=r.tmax_p_values or {},
            tmin_p_values=r.tmin_p_values or {},
            lead_time_degradation=r.lead_time_degradation or 0.15,
        )
        calibrations[r.station_id] = bias

    return calibrations


async def get_calibration_ages(session: AsyncSession) -> dict[str, datetime]:
    """
    Return the last_updated timestamp for each calibrated station.

    Used by freshness checks in the scheduler and live-paper daemon to warn
    when bias corrections are stale (i.e. not updated recently enough).
    """
    from sqlalchemy import select
    from weatheredge.models import StationCalibration

    result = await session.execute(
        select(StationCalibration.station_id, StationCalibration.last_updated)
    )
    return {row.station_id: row.last_updated for row in result}


async def warn_stale_calibrations(
    session: AsyncSession,
    *,
    max_age_days: int = 14,
    context: str = "",
) -> None:
    """
    Log a WARNING for every station whose calibration is older than max_age_days.

    Call this after loading calibrations anywhere the algo is about to trade.
    Does NOT raise — a stale calibration is better than no calibration.
    """
    ages = await get_calibration_ages(session)
    if not ages:
        logger.warning("%sNo calibrations found in DB.", f"[{context}] " if context else "")
        return

    now = datetime.utcnow()
    stale = {
        sid: lu for sid, lu in ages.items()
        if lu is None or (now - lu).days >= max_age_days
    }
    fresh = len(ages) - len(stale)

    if stale:
        worst = min(
            (lu for lu in stale.values() if lu is not None),
            default=None,
        )
        age_str = f"{(now - worst).days}d" if worst else "unknown"
        logger.warning(
            "%s%d/%d station calibrations are >%dd old (oldest: %s). "
            "Run `weatheredge calibrate` to refresh.",
            f"[{context}] " if context else "",
            len(stale),
            len(ages),
            max_age_days,
            age_str,
        )
    else:
        logger.debug(
            "%sAll %d station calibrations are fresh (within %dd).",
            f"[{context}] " if context else "",
            fresh,
            max_age_days,
        )


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------

async def get_next_market_price(
    session: AsyncSession,
    market_id: str,
    after_timestamp: datetime,
) -> dict[int, float] | None:
    """
    Fetch the first market price recorded AFTER a given timestamp.
    Used for paper trade slippage calculation (simulating price movement).
    """
    from sqlalchemy import select
    from weatheredge.models import MarketPrice

    stmt = (
        select(MarketPrice)
        .where(MarketPrice.market_id == market_id)
        .where(MarketPrice.recorded_at > after_timestamp)
        .order_by(MarketPrice.recorded_at.asc())
        .limit(1)
    )
    result = await session.execute(stmt)
    mp = result.scalar_one_or_none()

    if mp and mp.prices:
        return {int(k): float(v) for k, v in mp.prices.items()}
    return None


# ---------------------------------------------------------------------------
# Teardown (for tests / clean shutdown)
# ---------------------------------------------------------------------------

async def close_db() -> None:
    """Dispose of the engine and reset singletons."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine disposed.")