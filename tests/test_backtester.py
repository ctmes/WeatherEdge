"""
Tests for the backtester — fetchers, orchestrator, scoring, and disk cache.

All tests use an in-memory SQLite database to avoid side effects.
External API calls are mocked.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture: in-memory database for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def _setup_test_db():
    """Swap the global engine to an in-memory SQLite DB for each test."""
    import weatheredge.db as db_mod
    from weatheredge.models import Base

    db_mod._engine = None
    db_mod._session_factory = None

    with patch.object(db_mod, "DATABASE_URL", "sqlite+aiosqlite://"):
        db_mod._engine = None
        db_mod._session_factory = None

        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        db_mod._engine = engine
        db_mod._session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False,
        )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield

        await engine.dispose()
        db_mod._engine = None
        db_mod._session_factory = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gamma_market(
    condition_id: str = "bt-mkt-001",
    question: str = "What will the high temperature in Wellington be?",
    outcomes: str = "Under 18,18-20,20-22,Over 22",
    outcome_prices: str = "0.10,0.30,0.40,0.20",
    end_date: str = "2025-02-15T00:00:00Z",
    resolved_prices: str = "0.0,0.0,1.0,0.0",
) -> dict:
    """Build a mock Gamma API market dict."""
    return {
        "conditionId": condition_id,
        "question": question,
        "description": "Temperature measured at NZWN station.",
        "outcomes": outcomes,
        "outcomePrices": resolved_prices,  # post-resolution
        "clobTokenIds": "tok1,tok2,tok3,tok4",
        "volume": "15000",
        "endDate": end_date,
        "active": False,
        "closed": True,
        "tags": ["temperature", "weather"],
    }


# =========================================================================
# Disk cache tests
# =========================================================================

class TestDiskCache:
    def test_cache_key_deterministic(self):
        from weatheredge.backtest_fetchers import _cache_key
        k1 = _cache_key("a", "b", "c")
        k2 = _cache_key("a", "b", "c")
        assert k1 == k2
        assert k1.endswith(".json")

    def test_cache_key_differs(self):
        from weatheredge.backtest_fetchers import _cache_key
        k1 = _cache_key("a", "b")
        k2 = _cache_key("a", "c")
        assert k1 != k2

    def test_write_and_read_cache(self, tmp_path):
        from weatheredge.backtest_fetchers import _read_cache, _write_cache, _CACHE_DIR
        import weatheredge.backtest_fetchers as bf

        # Temporarily redirect cache dir
        original = bf._CACHE_DIR
        bf._CACHE_DIR = tmp_path

        try:
            _write_cache("test.json", {"hello": "world"})
            result = _read_cache("test.json")
            assert result is not None
            assert result["hello"] == "world"
        finally:
            bf._CACHE_DIR = original

    def test_read_cache_miss(self, tmp_path):
        from weatheredge.backtest_fetchers import _read_cache
        import weatheredge.backtest_fetchers as bf

        original = bf._CACHE_DIR
        bf._CACHE_DIR = tmp_path
        try:
            assert _read_cache("nonexistent.json") is None
        finally:
            bf._CACHE_DIR = original


# =========================================================================
# Fetcher tests
# =========================================================================

class TestResolvedMarketExtraction:
    def test_extract_resolved_outcome_clear_winner(self):
        from weatheredge.backtest_fetchers import extract_resolved_outcome
        raw = {"outcomePrices": "0.0,0.0,1.0,0.0"}
        assert extract_resolved_outcome(raw) == 2

    def test_extract_resolved_outcome_list_format(self):
        from weatheredge.backtest_fetchers import extract_resolved_outcome
        raw = {"outcomePrices": [0.0, 1.0, 0.0]}
        assert extract_resolved_outcome(raw) == 1

    def test_extract_resolved_outcome_unclear(self):
        from weatheredge.backtest_fetchers import extract_resolved_outcome
        raw = {"outcomePrices": "0.3,0.3,0.4"}
        # max price = 0.4, below 0.8 threshold
        assert extract_resolved_outcome(raw) is None

    def test_extract_resolved_outcome_missing(self):
        from weatheredge.backtest_fetchers import extract_resolved_outcome
        assert extract_resolved_outcome({}) is None


# =========================================================================
# Scoring tests
# =========================================================================

class TestBacktestScoring:
    def test_pnl_win(self):
        from weatheredge.backtester import _calculate_pnl
        from weatheredge.models import EdgeSignal

        sig = EdgeSignal(
            market_id="m1", question="Q", station_id="NZWN",
            bin_index=2, bin_label="20-22", bin_range=(20, 22),
            model_prob=0.6, market_prob=0.4,
            raw_edge=0.2, net_edge=0.18,
            kelly_fraction=0.045, recommended_bet_usd=450.0,
        )
        pnl = _calculate_pnl(sig, won=True)
        # gross = 450 * (1/0.4 - 1) = 450 * 1.5 = 675
        # net = 675 * 0.98 = 661.5
        assert abs(pnl - 661.5) < 0.01

    def test_pnl_loss(self):
        from weatheredge.backtester import _calculate_pnl
        from weatheredge.models import EdgeSignal

        sig = EdgeSignal(
            market_id="m1", question="Q", station_id="NZWN",
            bin_index=2, bin_label="20-22", bin_range=(20, 22),
            model_prob=0.6, market_prob=0.4,
            raw_edge=0.2, net_edge=0.18,
            kelly_fraction=0.045, recommended_bet_usd=450.0,
        )
        pnl = _calculate_pnl(sig, won=False)
        assert pnl == -450.0


# =========================================================================
# Orchestrator integration test (fully mocked)
# =========================================================================

class TestBacktestOrchestrator:
    async def test_empty_markets(self):
        """Backtest with no resolved markets should return cleanly."""
        from weatheredge.backtester import BacktestConfig, run_backtest

        with patch(
            "weatheredge.backtester.fetch_resolved_markets",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "weatheredge.backtester.fetch_resolved_daily_events",
            new_callable=AsyncMock,
            return_value=[],
        ):
            bt_run = await run_backtest({
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            })

        assert bt_run.markets_count == 0

    async def test_full_backtest_with_mock_data(self):
        """End-to-end: mock Gamma + forecast → expect BrierScore rows (brier_only default)."""
        from weatheredge.backtester import BacktestConfig, run_backtest
        from weatheredge.db import get_session
        from weatheredge.models import Signal, Result, BrierScore, BacktestRun, ForecastResult, ParsedMarket
        from sqlalchemy import select, func

        # Build a mock resolved market
        raw = _make_gamma_market()
        parsed = ParsedMarket(
            condition_id="bt-mkt-001",
            question="What will the high temperature in Wellington be?",
            outcomes=["Under 18", "18-20", "20-22", "Over 22"],
            outcome_prices=[0.10, 0.30, 0.40, 0.20],
            token_ids=["tok1", "tok2", "tok3", "tok4"],
            volume=15000,
            end_date="2025-02-15T00:00:00Z",
            description="Temperature measured at NZWN station.",
            station_id="NZWN",
            metric="tmax",
            bins=[(-float("inf"), 18), (18, 20), (20, 22), (22, float("inf"))],
            timezone="Pacific/Auckland",
            resolution_date=date(2025, 2, 15),
        )

        mock_forecast = ForecastResult(
            station_id="NZWN",
            valid_date=date(2025, 2, 15),
            tmax_mean=21.0,
            tmax_std=2.0,
            models_used=["ecmwf_ifs"],
        )

        with patch(
            "weatheredge.backtester.fetch_resolved_markets",
            new_callable=AsyncMock,
            return_value=[(parsed, raw)],
        ), patch(
            "weatheredge.backtester.fetch_resolved_daily_events",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "weatheredge.backtester.fetch_historical_forecast",
            new_callable=AsyncMock,
            return_value=mock_forecast,
        ):
            bt_run = await run_backtest({
                "start_date": "2025-02-01",
                "end_date": "2025-02-28",
                "bankroll": 10000,
                "model": "ecmwf_ifs",
                "mode": "brier_only",  # default mode
            })

        # Verify BacktestRun was completed
        assert bt_run.finished_at is not None

        # In brier_only mode: no signals, but Brier scores should be computed
        async with get_session() as session:
            sig_count = (await session.execute(
                select(func.count()).select_from(Signal).where(Signal.backtest_run_id == bt_run.id)
            )).scalar()
            brier_count = (await session.execute(
                select(func.count()).select_from(BrierScore).where(BrierScore.backtest_run_id == bt_run.id)
            )).scalar()

        # brier_only => zero signals but brier scores computed
        assert sig_count == 0
        assert brier_count >= 1

    async def test_backtest_run_stores_config(self):
        """Config JSON should be persisted in BacktestRun."""
        from weatheredge.backtester import run_backtest
        from weatheredge.db import get_session
        from weatheredge.models import BacktestRun
        from sqlalchemy import select

        cfg = {
            "start_date": "2025-03-01",
            "end_date": "2025-03-31",
            "bankroll": 5000,
            "model": "gfs_seamless",
        }

        with patch(
            "weatheredge.backtester.fetch_resolved_markets",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "weatheredge.backtester.fetch_resolved_daily_events",
            new_callable=AsyncMock,
            return_value=[],
        ):
            bt_run = await run_backtest(cfg)

        async with get_session() as session:
            result = await session.execute(
                select(BacktestRun).where(BacktestRun.id == bt_run.id)
            )
            row = result.scalar_one()

        assert row.config["bankroll"] == 5000
        assert row.config["model"] == "gfs_seamless"


# =========================================================================
# New tests: verify root cause fixes
# =========================================================================

class TestBacktesterFixes:
    """Tests that validate the root cause fixes from the council diagnosis."""

    async def test_no_uniform_price_override(self):
        """
        P0 FIX: Market prices should NOT be replaced with uniform 1/n.
        The parsed market's outcome_prices should be preserved.
        """
        from weatheredge.backtester import run_backtest
        from weatheredge.models import ForecastResult, ParsedMarket

        original_prices = [0.10, 0.30, 0.40, 0.20]
        raw = _make_gamma_market()
        parsed = ParsedMarket(
            condition_id="fix-test-001",
            question="What will the high temperature in Wellington be?",
            outcomes=["Under 18", "18-20", "20-22", "Over 22"],
            outcome_prices=original_prices.copy(),
            token_ids=["tok1", "tok2", "tok3", "tok4"],
            volume=15000,
            end_date="2025-02-15T00:00:00Z",
            description="Temperature measured at NZWN station.",
            station_id="NZWN",
            metric="tmax",
            bins=[(-float("inf"), 18), (18, 20), (20, 22), (22, float("inf"))],
            timezone="Pacific/Auckland",
            resolution_date=date(2025, 2, 15),
        )

        mock_forecast = ForecastResult(
            station_id="NZWN",
            valid_date=date(2025, 2, 15),
            tmax_mean=21.0,
            tmax_std=2.0,
            models_used=["ecmwf_ifs"],
        )

        with patch(
            "weatheredge.backtester.fetch_resolved_markets",
            new_callable=AsyncMock,
            return_value=[(parsed, raw)],
        ), patch(
            "weatheredge.backtester.fetch_resolved_daily_events",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "weatheredge.backtester.fetch_historical_forecast",
            new_callable=AsyncMock,
            return_value=mock_forecast,
        ):
            await run_backtest({
                "start_date": "2025-02-01",
                "end_date": "2025-02-28",
                "mode": "full_sim",
            })

        # After backtesting, prices must NOT have been replaced with uniform [0.25, 0.25, 0.25, 0.25]
        assert parsed.outcome_prices != [0.25, 0.25, 0.25, 0.25], \
            "REGRESSION: uniform price override is still active!"

    async def test_brier_only_produces_no_signals(self):
        """
        P3 FIX: In brier_only mode, no edge/Kelly computations happen.
        Only Brier scores are computed.
        """
        from weatheredge.backtester import run_backtest
        from weatheredge.db import get_session
        from weatheredge.models import Signal, BrierScore, ForecastResult, ParsedMarket
        from sqlalchemy import select, func

        raw = _make_gamma_market()
        parsed = ParsedMarket(
            condition_id="brier-test-001",
            question="What will the high temperature in Wellington be?",
            outcomes=["Under 18", "18-20", "20-22", "Over 22"],
            outcome_prices=[0.10, 0.30, 0.40, 0.20],
            token_ids=["tok1", "tok2", "tok3", "tok4"],
            volume=15000,
            end_date="2025-02-15T00:00:00Z",
            description="Temperature measured at NZWN station.",
            station_id="NZWN",
            metric="tmax",
            bins=[(-float("inf"), 18), (18, 20), (20, 22), (22, float("inf"))],
            timezone="Pacific/Auckland",
            resolution_date=date(2025, 2, 15),
        )

        mock_forecast = ForecastResult(
            station_id="NZWN",
            valid_date=date(2025, 2, 15),
            tmax_mean=21.0,
            tmax_std=2.0,
            models_used=["ecmwf_ifs"],
        )

        with patch(
            "weatheredge.backtester.fetch_resolved_markets",
            new_callable=AsyncMock,
            return_value=[(parsed, raw)],
        ), patch(
            "weatheredge.backtester.fetch_resolved_daily_events",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "weatheredge.backtester.fetch_historical_forecast",
            new_callable=AsyncMock,
            return_value=mock_forecast,
        ):
            bt_run = await run_backtest({
                "start_date": "2025-02-01",
                "end_date": "2025-02-28",
                "mode": "brier_only",
            })

        async with get_session() as session:
            sig_count = (await session.execute(
                select(func.count()).select_from(Signal).where(Signal.backtest_run_id == bt_run.id)
            )).scalar()
            brier_count = (await session.execute(
                select(func.count()).select_from(BrierScore).where(BrierScore.backtest_run_id == bt_run.id)
            )).scalar()

        assert sig_count == 0, f"brier_only mode should produce 0 signals, got {sig_count}"
        assert brier_count >= 1, f"brier_only mode should produce Brier scores, got {brier_count}"

    async def test_calibration_passed_to_process_market(self):
        """
        P1 FIX: Calibration data from config should be passed through to _process_market.
        """
        from weatheredge.backtester import run_backtest
        from weatheredge.calibration import StationBias
        from weatheredge.models import ForecastResult, ParsedMarket

        raw = _make_gamma_market()
        parsed = ParsedMarket(
            condition_id="cal-test-001",
            question="What will the high temperature in Wellington be?",
            outcomes=["Under 18", "18-20", "20-22", "Over 22"],
            outcome_prices=[0.10, 0.30, 0.40, 0.20],
            token_ids=["tok1", "tok2", "tok3", "tok4"],
            volume=15000,
            end_date="2025-02-15T00:00:00Z",
            description="Temperature measured at NZWN station.",
            station_id="NZWN",
            metric="tmax",
            bins=[(-float("inf"), 18), (18, 20), (20, 22), (22, float("inf"))],
            timezone="Pacific/Auckland",
            resolution_date=date(2025, 2, 15),
        )

        mock_forecast = ForecastResult(
            station_id="NZWN",
            valid_date=date(2025, 2, 15),
            tmax_mean=21.0,
            tmax_std=2.0,
            models_used=["ecmwf_ifs"],
        )

        test_bias = StationBias(
            station_id="NZWN",
            tmax_bias_summer=-1.5,
            tmin_bias_summer=-0.8,
            tmax_rmse=2.0,
            tmin_rmse=1.8,
        )

        received_calibrations = []
        original_process = None

        # Spy on _process_market to capture the calibration arg
        import weatheredge.backtester as bt_mod
        original_process = bt_mod._process_market

        def spy_process_market(*args, **kwargs):
            # 4th positional arg is calibration (after market, fc_by_date, rg_by_date)
            if len(args) > 3:
                received_calibrations.append(args[3])
            return original_process(*args, **kwargs)

        with patch(
            "weatheredge.backtester.fetch_resolved_markets",
            new_callable=AsyncMock,
            return_value=[(parsed, raw)],
        ), patch(
            "weatheredge.backtester.fetch_resolved_daily_events",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "weatheredge.backtester.fetch_historical_forecast",
            new_callable=AsyncMock,
            return_value=mock_forecast,
        ), patch(
            "weatheredge.backtester._process_market",
            side_effect=spy_process_market,
        ):
            await run_backtest({
                "start_date": "2025-02-01",
                "end_date": "2025-02-28",
                "mode": "full_sim",
                "calibrations": {"NZWN": test_bias},
            })

        # Verify calibration was actually passed through
        assert len(received_calibrations) > 0, "_process_market was never called"
        assert received_calibrations[0] is test_bias, \
            f"Expected StationBias for NZWN, got {received_calibrations[0]}"
