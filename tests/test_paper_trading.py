"""
Tests for the paper-trading infrastructure:
  - Database bootstrap (db.py)
  - Paper-trade logger (logger.py)
  - Resolution tracker (resolver.py)
  - PnL calculation + Brier scoring

All tests use an in-memory SQLite database to avoid side effects.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture: in-memory database for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def _setup_test_db():
    """Swap the global engine to an in-memory SQLite DB for each test."""
    import weatheredge.db as db_mod
    from weatheredge.models import Base

    # Reset singletons
    db_mod._engine = None
    db_mod._session_factory = None

    # Patch DATABASE_URL to use in-memory SQLite
    with patch.object(db_mod, "DATABASE_URL", "sqlite+aiosqlite://"):
        # Force re-creation with the patched URL
        db_mod._engine = None
        db_mod._session_factory = None

        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        db_mod._engine = engine
        db_mod._session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False,
        )

        # Create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield

        # Teardown
        await engine.dispose()
        db_mod._engine = None
        db_mod._session_factory = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    market_id: str = "test-market-001",
    bin_index: int = 2,
    model_prob: float = 0.60,
    market_prob: float = 0.40,
    net_edge: float = 0.18,
    kelly: float = 0.045,
    bet_usd: float = 450.0,
):
    """Create a minimal EdgeSignal for testing."""
    from weatheredge.models import EdgeSignal
    return EdgeSignal(
        market_id=market_id,
        question="What will the high temperature in Wellington be?",
        station_id="NZWN",
        bin_index=bin_index,
        bin_label="20-22°C",
        bin_range=(20.0, 22.0),
        model_prob=model_prob,
        market_prob=market_prob,
        raw_edge=model_prob - market_prob,
        net_edge=net_edge,
        kelly_fraction=kelly,
        recommended_bet_usd=bet_usd,
    )


# =========================================================================
# Database bootstrap tests
# =========================================================================

class TestDatabase:
    async def test_init_db_creates_tables(self):
        from weatheredge.db import get_session
        from sqlalchemy import text

        async with get_session() as session:
            result = await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
            tables = {row[0] for row in result.fetchall()}

        assert "markets" in tables
        assert "signals" in tables
        assert "results" in tables
        assert "brier_scores" in tables
        assert "market_prices" in tables
        assert "forecasts" in tables

    async def test_get_session_commits(self):
        from weatheredge.db import get_session
        from weatheredge.models import Market

        async with get_session() as session:
            session.add(Market(id="m1", question="Test?"))

        # Read back in a new session
        async with get_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(Market).where(Market.id == "m1"))
            market = result.scalar_one_or_none()

        assert market is not None
        assert market.question == "Test?"


# =========================================================================
# Logger tests
# =========================================================================

class TestLogger:
    async def test_log_scan_persists_signals(self):
        from weatheredge.logger import log_scan
        from weatheredge.db import get_session
        from weatheredge.models import Signal
        from sqlalchemy import select

        signals = [_make_signal(), _make_signal(bin_index=3)]
        written = await log_scan(signals)

        assert written == 2

        async with get_session() as session:
            result = await session.execute(select(Signal))
            rows = result.scalars().all()

        assert len(rows) == 2
        assert rows[0].signal_type == "paper"

    async def test_log_scan_creates_market(self):
        from weatheredge.logger import log_scan
        from weatheredge.db import get_session
        from weatheredge.models import Market
        from sqlalchemy import select

        await log_scan([_make_signal()])

        async with get_session() as session:
            result = await session.execute(select(Market))
            market = result.scalar_one_or_none()

        assert market is not None
        assert market.id == "test-market-001"
        assert market.station_id == "NZWN"

    async def test_log_scan_upsert_no_duplicate(self):
        """Logging the same market twice should not create duplicate Market rows."""
        from weatheredge.logger import log_scan
        from weatheredge.db import get_session
        from weatheredge.models import Market
        from sqlalchemy import select, func

        await log_scan([_make_signal()])
        await log_scan([_make_signal(bin_index=4)])

        async with get_session() as session:
            result = await session.execute(select(func.count()).select_from(Market))
            count = result.scalar()

        assert count == 1  # only one market, not two

    async def test_log_scan_records_timestamps(self):
        from weatheredge.logger import log_scan
        from weatheredge.db import get_session
        from weatheredge.models import Signal
        from sqlalchemy import select

        scan_time = datetime(2026, 3, 2, 12, 0, 0)
        await log_scan([_make_signal()], scan_time=scan_time)

        async with get_session() as session:
            result = await session.execute(select(Signal))
            sig = result.scalar_one()

        assert sig.created_at == scan_time

    async def test_log_scan_empty_signals(self):
        from weatheredge.logger import log_scan
        written = await log_scan([])
        assert written == 0

    async def test_log_scan_preserves_edge_data(self):
        from weatheredge.logger import log_scan
        from weatheredge.db import get_session
        from weatheredge.models import Signal
        from sqlalchemy import select

        sig = _make_signal(model_prob=0.72, market_prob=0.45, net_edge=0.25)
        await log_scan([sig])

        async with get_session() as session:
            result = await session.execute(select(Signal))
            row = result.scalar_one()

        assert abs(row.model_prob - 0.72) < 0.001
        assert abs(row.market_prob - 0.45) < 0.001
        assert abs(row.edge - 0.25) < 0.001


# =========================================================================
# Resolver tests
# =========================================================================

class TestResolver:
    async def test_pnl_calculation_win(self):
        from weatheredge.resolver import _calculate_paper_pnl
        from weatheredge.models import Signal

        sig = Signal(
            market_id="m1", bin_index=2,
            model_prob=0.6, market_prob=0.4,
            edge=0.18, kelly_fraction=0.045,
            bet_size_usd=400, signal_type="paper",
        )
        pnl = _calculate_paper_pnl(sig, won=True)
        # Win: 400 * (1/0.4 - 1) = 400 * 1.5 = 600 gross
        # Fee: 600 * 0.02 = 12
        # Net: 600 - 12 = 588
        assert abs(pnl - 588.0) < 0.01

    async def test_pnl_calculation_loss(self):
        from weatheredge.resolver import _calculate_paper_pnl
        from weatheredge.models import Signal

        sig = Signal(
            market_id="m1", bin_index=2,
            model_prob=0.6, market_prob=0.4,
            edge=0.18, kelly_fraction=0.045,
            bet_size_usd=400, signal_type="paper",
        )
        pnl = _calculate_paper_pnl(sig, won=False)
        assert pnl == -400.0

    async def test_brier_score_calculation(self):
        """Brier score should match known values."""
        from weatheredge.probability import brier_score

        # Perfect prediction: [0, 0, 1, 0, 0] for bin 2
        score = brier_score([0, 0, 1, 0, 0], 2)
        assert score == 0.0  # perfect

        # Worst case: [0, 0, 0, 0, 1] for bin 2
        score = brier_score([0, 0, 0, 0, 1], 2)
        assert score == 2.0  # worst possible

        # Even spread: [0.2, 0.2, 0.2, 0.2, 0.2] for bin 2
        score = brier_score([0.2, 0.2, 0.2, 0.2, 0.2], 2)
        expected = 4 * (0.2**2) + (0.2 - 1)**2  # 4*0.04 + 0.64 = 0.80
        assert abs(score - expected) < 0.001

    async def test_check_resolutions_no_signals(self):
        from weatheredge.resolver import check_resolutions
        resolved = await check_resolutions()
        assert resolved == 0

    async def test_check_resolutions_with_signals(self):
        """Signals exist but market not yet resolved → returns 0."""
        from weatheredge.logger import log_scan
        from weatheredge.resolver import check_resolutions

        # Log a signal first
        await log_scan([_make_signal()])

        # Mock the API call to return None (not resolved)
        with patch(
            "weatheredge.resolver._fetch_resolved_outcome",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resolved = await check_resolutions()

        assert resolved == 0

    async def test_check_resolutions_resolves_signal(self):
        """When market is resolved, Result + BrierScore rows are written."""
        from weatheredge.logger import log_scan
        from weatheredge.resolver import check_resolutions
        from weatheredge.db import get_session
        from weatheredge.models import Result, BrierScore
        from sqlalchemy import select, func

        await log_scan([_make_signal(bin_index=2)])

        # Mock the API to say bin 2 won
        with patch(
            "weatheredge.resolver._fetch_resolved_outcome",
            new_callable=AsyncMock,
            return_value={"resolved_bin": 2},
        ):
            resolved = await check_resolutions()

        assert resolved == 1

        # Check Result was written
        async with get_session() as session:
            result = await session.execute(select(Result))
            row = result.scalar_one()

        assert row.won is True
        assert row.pnl_usd > 0

        # Check BrierScore was written
        async with get_session() as session:
            result = await session.execute(select(func.count()).select_from(BrierScore))
            count = result.scalar()

        assert count == 1


# =========================================================================
# Scheduler tests (mocked, no real API calls)
# =========================================================================

class TestScheduler:
    async def test_single_cycle_runs(self):
        """run_loop(once=True) should complete without error."""
        from weatheredge.scheduler import run_loop

        with patch(
            "weatheredge.scheduler.run_scan",
            new_callable=AsyncMock,
            return_value=([], None),
        ):
            await run_loop(once=True)

    async def test_single_cycle_logs_signals(self):
        """Signals from run_scan should be passed to log_scan."""
        from weatheredge.scheduler import run_loop

        signals = [_make_signal()]
        with patch(
            "weatheredge.scheduler.run_scan",
            new_callable=AsyncMock,
            return_value=(signals, None),
        ) as mock_scan, patch(
            "weatheredge.scheduler.log_scan",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_log:
            await run_loop(once=True)

        mock_scan.assert_called_once()
        mock_log.assert_called_once()
        logged_signals = mock_log.call_args[0][0]
        assert len(logged_signals) == 1
