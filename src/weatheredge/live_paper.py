"""
Live Paper Trading Orchestrator - Real-time execution with WebSocket.

Execution order per market, every cycle:
  0. Find market (fresh from Gamma, refreshed every market_refresh_interval)
  1. Load calibrations (refreshed from DB every market_refresh_interval)
  2. Fetch forecast + regime (refreshed every market_refresh_interval)
  3. Run _process_market (edge calculation using live orderbook price)
  4. If edge >= threshold, execute_signal immediately (no sleep between steps 3-4)

Forecast / calibration data is never older than market_refresh_interval seconds
when a trade fires. The live orderbook price is always fresh (WebSocket, seconds old).
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from weatheredge.db import init_db, get_session, get_calibrations, warn_stale_calibrations
from weatheredge.logger import log_scan
from weatheredge.pipeline import fetch_weather_markets, get_station, fetch_forecast, fetch_regime_data, _process_market, AtmosphericRegime
from weatheredge.ws_client import WeatherEdgeMarketWS, OrderbookSnapshot, TradeEvent
from weatheredge.models import ParsedMarket, ForecastResult
from weatheredge.execution import execute_signal, load_open_positions, clear_executed_signals
from weatheredge.config import TRADING

logger = logging.getLogger("weatheredge.live_paper")

# Maximum age of per-station forecast data before we refuse to trade that station.
# A station whose forecast fetch failed silently will have no entry here and be skipped.
_FORECAST_MAX_AGE_SECS = 7200  # 2 × default 3600s refresh interval


async def run_live_paper(
    bankroll: float = 10_000.0,
    update_interval: float = 10.0,       # seconds between signal evaluation cycles
    market_refresh_interval: float = 3600.0,  # seconds between full metadata refreshes
):
    """
    Run the live paper trading daemon.
    """
    await init_db()

    async with get_session() as session:
        calibrations = await get_calibrations(session)

    # Seed the execution deduplication cache from DB so positions placed in
    # prior runs are not re-fired after a restart or redeployment.
    await load_open_positions()

    ws = WeatherEdgeMarketWS()

    # State
    markets: List[ParsedMarket] = []
    forecasts: Dict[str, List[ForecastResult]] = {}
    regime_data: Dict[str, List[AtmosphericRegime]] = {}
    # Per-station timestamp of the last successful forecast fetch.
    # Used to refuse trading a station when its data is stale.
    forecast_fetched_at: Dict[str, datetime] = {}
    last_market_refresh = datetime.min

    async def refresh_metadata():
        nonlocal markets, forecasts, regime_data, calibrations, last_market_refresh
        logger.info("Refreshing market metadata, forecasts, and calibrations...")

        # Wrap the entire refresh so a transient API failure does not crash the
        # main loop — it just retries on the next cycle.
        try:
            new_markets = await fetch_weather_markets()
            markets = [m for m in new_markets if m.station_id]

            all_token_ids = []
            for m in markets:
                all_token_ids.extend(m.token_ids)
                if not m.token_ids:
                    all_token_ids.append(m.condition_id)

            await ws.subscribe(all_token_ids)

            unique_stations = {m.station_id for m in markets if m.station_id}
            for sid in unique_stations:
                station = get_station(sid)
                if not station:
                    continue
                try:
                    fc_results, rg_results = await asyncio.gather(
                        fetch_forecast(station.latitude, station.longitude, station.timezone),
                        fetch_regime_data(station.latitude, station.longitude, station.timezone),
                    )
                    for r in fc_results:
                        r.station_id = sid
                    for r in rg_results:
                        r.station_id = sid
                    forecasts[sid] = fc_results
                    regime_data[sid] = rg_results
                    forecast_fetched_at[sid] = datetime.utcnow()
                except Exception as e:
                    logger.warning("Forecast fetch failed for %s: %s", sid, e)
                    # Do NOT update forecast_fetched_at — the staleness guard will
                    # block trading for this station until a successful fetch.

            # Step 1: reload calibrations from DB so bias corrections are always
            # current. This is the "update historical biases" step — it runs every
            # market_refresh_interval seconds, not just at startup.
            async with get_session() as session:
                calibrations = await get_calibrations(session)
                await warn_stale_calibrations(session, max_age_days=14, context="live_paper")
            logger.info(
                "Calibrations reloaded: %d stations.", len(calibrations)
            )

            last_market_refresh = datetime.utcnow()
            logger.info("Metadata refresh complete for %d stations.", len(unique_stations))

            # Clear the in-memory dedup cache after a refresh — resolved markets
            # will have dropped out of the open-position query so the DB re-seed
            # on the next execution will correctly allow fresh signals on new markets.
            clear_executed_signals()

        except Exception:
            logger.exception(
                "refresh_metadata failed — retrying at next cycle in %.0fs",
                market_refresh_interval,
            )

    async def on_trade(trade: TradeEvent):
        target_market = None
        for m in markets:
            if trade.asset_id in m.token_ids or trade.asset_id == m.condition_id:
                target_market = m
                break

        if target_market is None:
            logger.debug("on_trade: no market found for asset_id=%s", trade.asset_id)
            return

        station_str = (
            "[%s] " % target_market.station_id
            if target_market.station_id
            else ""
        )

        logger.info(
            "TRADE %s%s %.1f shares @ $%.3f",
            station_str,
            trade.side.upper(),
            trade.size,
            trade.price,
        )

    ws.on_trade = on_trade

    # Add a done-callback so unexpected exceptions from ws.run() are surfaced
    # immediately rather than silently swallowed until shutdown.
    ws_task = asyncio.create_task(ws.run())

    def _ws_task_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("ws.run() raised an unexpected exception: %s", exc, exc_info=exc)

    ws_task.add_done_callback(_ws_task_done)

    try:
        while True:
            now = datetime.utcnow()

            # Step 0+1+2: refresh markets, forecasts, and calibrations together.
            # All three must be fresh before any trade decision is made.
            if (now - last_market_refresh).total_seconds() > market_refresh_interval:
                await refresh_metadata()

            if not markets:
                await asyncio.sleep(5)
                continue

            # Steps 3+4: run algo and immediately execute — no sleep between them.
            # Market price comes from the live WebSocket orderbook (seconds old).
            # Forecast and calibration come from the most recent refresh.
            all_signals = []
            for m in markets:
                sid = m.station_id
                if sid not in forecasts:
                    continue

                # Staleness guard: refuse to trade if this station's forecast is
                # older than _FORECAST_MAX_AGE_SECS. This catches the case where
                # refresh_metadata ran but the per-station fetch failed silently.
                fetched_at = forecast_fetched_at.get(sid)
                if fetched_at is None:
                    logger.debug("Skipping %s — no forecast fetch timestamp recorded yet.", sid)
                    continue
                forecast_age_secs = (now - fetched_at).total_seconds()
                if forecast_age_secs > _FORECAST_MAX_AGE_SECS:
                    logger.warning(
                        "Skipping %s — forecast is %.0fs old (max %ds). "
                        "Will retry after next metadata refresh.",
                        sid, forecast_age_secs, _FORECAST_MAX_AGE_SECS,
                    )
                    continue

                fc_by_date = {f.valid_date: f for f in forecasts[sid]}
                rg_by_date = {r.valid_date: r for r in regime_data[sid]}
                station_cal = calibrations.get(sid)

                # Use get_valid_orderbook() so StaleDataGuard is honoured;
                # passing ws._orderbooks directly bypasses it.
                valid_orderbooks = {
                    tid: ob
                    for tid in (m.token_ids or [m.condition_id])
                    if (ob := ws.get_valid_orderbook(tid)) is not None
                }

                # Step 3: run algo — price used here is the live orderbook mid.
                signals, _ = _process_market(
                    m, fc_by_date, rg_by_date, station_cal, bankroll,
                    orderbooks=valid_orderbooks,
                )
                all_signals.extend(signals)

            if all_signals:
                logger.info("Live Paper Cycle: %d signals found.", len(all_signals))
                # Step 4: execute immediately — the price we decided on is the
                # same price we act on. No re-fetch, no sleep.
                for signal in all_signals:
                    await execute_signal(
                        signal, bankroll,
                        live=TRADING.live_trading_enabled,
                    )

            await asyncio.sleep(update_interval)

    except asyncio.CancelledError:
        logger.info("Live Paper Trading stopped.")
    finally:
        await ws.stop()

        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
