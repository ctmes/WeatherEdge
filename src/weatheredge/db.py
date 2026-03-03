"""
Async SQLAlchemy database layer — engine, session factory, and DDL bootstrap.

Uses the DATABASE_URL from config (defaults to local SQLite file).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

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
    """Create all tables defined in ``models.Base.metadata``."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialised.")


async def upsert_market(session: AsyncSession, market_data: dict) -> None:
    """
    Upsert a Market row using on_conflict_do_update (idempotent).

    ``market_data`` must include ``id`` (primary key) plus any columns
    to insert/update.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from weatheredge.models import Market

    stmt = sqlite_insert(Market).values(**market_data)
    # On conflict, update mutable fields but keep created_at
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
