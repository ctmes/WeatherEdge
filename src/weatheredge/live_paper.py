"""
Live Paper Trading Orchestrator - Real-time execution with WebSocket.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from weatheredge.db import init_db, get_session, get_calibrations
from weatheredge.logger import log_scan
from weatheredge.pipeline import fetch_weather_markets, get_station, fetch_forecast, fetch_regime_data, _process_market, AtmosphericRegime
from weatheredge.ws_client import WeatherEdgeMarketWS, OrderbookSnapshot, TradeEvent
from weatheredge.models import ParsedMarket, ForecastResult
from weatheredge.execution import execute_signal, load_open_positions, clear_executed_signals
from weatheredge.config import TRADING

logger = logging.getLogger("weatheredge.live_paper")


async def run_live_paper(
    bankroll: float = 10_000.0,
    update_interval: float = 10.0,       # seconds
    market_refresh_interval: float = 3600.0,  # seconds
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
    last_market_refresh = datetime.min

    async def refresh_metadata():
        nonlocal markets, forecasts, regime_data, last_market_refresh
        logger.info("Refreshing market and forecast metadata...")

        # FIX #5: wrap the entire refresh in try/except so a transient API
        # failure does not crash the main loop — it just retries next cycle.
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
                except Exception as e:
                    # FIX #6: %s-style logging
                    logger.warning("Metadata fetch failed for %s: %s", sid, e)

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
        # FIX #8: log unmatched trades rather than silently ignoring them
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

        # FIX #7: use logger instead of print() for trade output
        logger.info(
            "TRADE %s%s %.1f shares @ $%.3f",
            station_str,
            trade.side.upper(),
            trade.size,
            trade.price,
        )

    ws.on_trade = on_trade

    # FIX #4: add a done-callback so unexpected exceptions from ws.run()
    # are surfaced immediately rather than silently swallowed until shutdown.
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

            if (now - last_market_refresh).total_seconds() > market_refresh_interval:
                await refresh_metadata()

            if not markets:
                await asyncio.sleep(5)
                continue

            all_signals = []
            for m in markets:
                sid = m.station_id
                if sid not in forecasts:
                    continue

                fc_by_date = {f.valid_date: f for f in forecasts[sid]}
                rg_by_date = {r.valid_date: r for r in regime_data[sid]}
                station_cal = calibrations.get(sid)

                # FIX #2: use get_valid_orderbook() so StaleDataGuard is
                # honoured; passing ws._orderbooks directly bypasses it.
                valid_orderbooks = {
                    tid: ob
                    for tid in (m.token_ids or [m.condition_id])
                    if (ob := ws.get_valid_orderbook(tid)) is not None
                }

                signals, _ = _process_market(
                    m, fc_by_date, rg_by_date, station_cal, bankroll,
                    orderbooks=valid_orderbooks,
                )
                all_signals.extend(signals)

            if all_signals:
                # FIX #6: %s-style logging
                logger.info("Live Paper Cycle: %d signals found.", len(all_signals))
                for signal in all_signals:
                    await execute_signal(
                        signal, bankroll,
                        live=TRADING.live_trading_enabled,
                    )

            await asyncio.sleep(update_interval)

    except asyncio.CancelledError:
        logger.info("Live Paper Trading stopped.")
    finally:
        # FIX #1: await the now-async stop() instead of calling it bare
        await ws.stop()

        # FIX #3: cancel the WS task and wait for it to finish cleanly
        # rather than just awaiting it, which would hang if run() is still
        # looping.
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass