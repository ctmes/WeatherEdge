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
import json
import logging
import signal as _signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from weatheredge.db import init_db, get_session, get_calibrations, warn_stale_calibrations, get_pnl_summary, get_exposure_summary
from weatheredge.logger import log_scan
from weatheredge.pipeline import fetch_weather_markets, get_station, fetch_forecast, fetch_regime_data, _process_market, AtmosphericRegime
from weatheredge.ws_client import WeatherEdgeMarketWS, OrderbookSnapshot, TradeEvent
from weatheredge.models import ParsedMarket, ForecastResult
from weatheredge.execution import execute_signal, load_open_positions, clear_executed_signals
from weatheredge.config import TRADING, DATABASE_URL
from weatheredge.safety import CircuitBreaker, check_position_limits

logger = logging.getLogger("weatheredge.live_paper")

_RESOLVE_INTERVAL_SECS = 1800  # 30 minutes


def _resolve_state_path() -> Path:
    raw = DATABASE_URL
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if raw.startswith(prefix):
            return Path(raw[len(prefix):]).parent / ".resolution_state.json"
    return Path("weatheredge.db").parent / ".resolution_state.json"


def _write_resolve_state(last_run: datetime, resolved_count: int) -> None:
    try:
        _resolve_state_path().write_text(
            json.dumps({"last_run": last_run.isoformat(), "resolved_count": resolved_count})
        )
    except Exception:
        pass  # non-critical

# Maximum age of per-station forecast data before we refuse to trade that station.
# A station whose forecast fetch failed silently will have no entry here and be skipped.
_FORECAST_MAX_AGE_SECS = 3600  # 1 × default refresh interval (Meteorologist: 2h is too stale)


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
    circuit_breaker = CircuitBreaker()

    # Mutable state shared between the main loop and inner callbacks.
    _state: Dict[str, object] = {
        "ws_failed": False,
        "circuit_status": "OK",
    }

    # State
    markets: List[ParsedMarket] = []
    forecasts: Dict[str, List[ForecastResult]] = {}
    regime_data: Dict[str, List[AtmosphericRegime]] = {}
    # Per-station timestamp of the last successful forecast fetch.
    # Used to refuse trading a station when its data is stale.
    forecast_fetched_at: Dict[str, datetime] = {}
    last_market_refresh = datetime.min
    last_resolution_check = datetime.min

    async def refresh_metadata():
        nonlocal markets, forecasts, regime_data, calibrations, last_market_refresh
        logger.info("Refreshing market metadata, forecasts, and calibrations...")

        # Wrap the entire refresh so a transient API failure does not crash the
        # main loop — it just retries on the next cycle.
        try:
            new_markets = await fetch_weather_markets()
            markets = [m for m in new_markets if m.station_id]

            today = datetime.utcnow().date()
            all_token_ids = []
            for m in markets:
                # Skip markets whose resolution date is already past — we don't
                # need live prices for them, only resolution checks.
                if m.resolution_date and m.resolution_date < today:
                    continue
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

            # ── Circuit breaker check ─────────────────────────────
            try:
                async with get_session() as session:
                    pnl = await get_pnl_summary(session, bankroll)
                cb_status, cb_msg = circuit_breaker.check(
                    bankroll=pnl["current_bankroll"],
                    daily_pnl=pnl["daily_pnl"],
                    weekly_pnl=pnl["weekly_pnl"],
                    monthly_pnl=pnl["monthly_pnl"],
                    peak_bankroll=pnl["peak_bankroll"],
                )
                if cb_status != "OK":
                    logger.critical("CIRCUIT BREAKER — %s: %s", cb_status, cb_msg)
                _state["circuit_status"] = cb_status
            except Exception:
                logger.critical(
                    "Circuit breaker check failed — pausing trading as a precaution. "
                    "Fix the underlying error and restart the daemon.",
                    exc_info=True,
                )
                _state["circuit_status"] = "PAUSE"

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

    # Add a done-callback that sets _state["ws_failed"] so the main loop can
    # exit cleanly rather than continuing with a dead orderbook connection.
    ws_task = asyncio.create_task(ws.run())

    def _ws_task_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.critical(
                "ws.run() crashed — stopping live-paper daemon: %s", exc, exc_info=exc,
            )
            _state["ws_failed"] = True

    ws_task.add_done_callback(_ws_task_done)

    _running = True

    def _stop():
        nonlocal _running
        logger.info("Received shutdown signal — stopping live-paper daemon after current cycle.")
        _running = False

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(_signal.SIGTERM, _stop)
    loop.add_signal_handler(_signal.SIGINT, _stop)

    try:
        while _running:
            # ── Safety guards: check before every evaluation cycle ─────────
            if _state["ws_failed"]:
                logger.critical(
                    "WebSocket connection failed — exiting live-paper daemon. "
                    "Process manager should restart the service."
                )
                break

            if _state["circuit_status"] in ("SHUTDOWN", "REVIEW", "PAUSE"):
                logger.critical(
                    "Circuit breaker tripped (%s) — exiting live-paper daemon. "
                    "Review logs and restart manually.",
                    _state["circuit_status"],
                )
                break

            now = datetime.utcnow()

            # Step 0+1+2: refresh markets, forecasts, and calibrations together.
            # All three must be fresh before any trade decision is made.
            if (now - last_market_refresh).total_seconds() > market_refresh_interval:
                await refresh_metadata()

            if (now - last_resolution_check).total_seconds() > _RESOLVE_INTERVAL_SECS:
                _resolved_count = 0
                try:
                    from weatheredge.resolver import check_resolutions
                    _resolved_count = await check_resolutions()
                    if _resolved_count:
                        logger.info("Resolved %d signal(s).", _resolved_count)
                except Exception:
                    logger.exception("Resolution check failed — will retry next cycle.")
                last_resolution_check = datetime.utcnow()
                _write_resolve_state(last_resolution_check, _resolved_count)

            if not markets:
                await asyncio.sleep(5)
                continue

            # Steps 3+4: run algo and immediately execute — no sleep between them.
            # Market price comes from the live WebSocket orderbook (seconds old).
            # Forecast and calibration come from the most recent refresh.
            all_signals = []
            for m in markets:
                sid = m.station_id
                # DIAGNOSTIC: log station→market mapping to detect station_id collisions
                # (multiple markets from different cities sharing the same station_id
                # would produce identical model probs — a sign of a bug in enrichment).
                logger.debug(
                    "Evaluating market=%s station_id=%s question=%s",
                    m.condition_id, sid, m.question[:60],
                )
                if sid not in forecasts:
                    continue

                # Staleness guard: refuse to trade if this station's forecast is
                # older than _FORECAST_MAX_AGE_SECS. This catches the case where
                # refresh_metadata ran but the per-station fetch failed silently.
                fetched_at = forecast_fetched_at.get(sid)
                if fetched_at is None:
                    logger.warning(
                        "Skipping %s — forecast has never been successfully fetched. "
                        "Check API connectivity; will retry after next metadata refresh.",
                        sid,
                    )
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

                # Live trading requires live prices — skip any market where the
                # WebSocket hasn't delivered a fresh snapshot yet (e.g. race
                # condition immediately after subscription, or stale connection).
                if not valid_orderbooks:
                    logger.debug(
                        "Skipping %s — no live orderbook snapshot available yet",
                        m.question[:60],
                    )
                    continue

                # Step 3: run algo — price used here is the live orderbook mid.
                # Pass current DB exposure so position limits are checked against
                # real open positions, not the default 0.0 (which always passes).
                async with get_session() as _exp_session:
                    _exp = await get_exposure_summary(_exp_session, market_id=m.condition_id)
                signals, _ = _process_market(
                    m, fc_by_date, rg_by_date, station_cal, bankroll,
                    current_market_exposure=_exp["market_exposure"],
                    current_total_exposure=_exp["total_exposure"],
                    orderbooks=valid_orderbooks,
                    require_live=True,
                )
                all_signals.extend(signals)

            if all_signals:
                logger.info("Live Paper Cycle: %d signals found.", len(all_signals))
                # Step 4: execute immediately — the price we decided on is the
                # same price we act on. No re-fetch, no sleep.
                for signal in all_signals:
                    # Re-check total exposure before each execution — multiple
                    # signals can fire in the same cycle and each one changes
                    # the total deployment.
                    async with get_session() as _chk_session:
                        _chk = await get_exposure_summary(_chk_session, market_id=signal.market_id)
                    allowed, reason = check_position_limits(
                        signal.recommended_bet_usd,
                        bankroll,
                        _chk["market_exposure"],
                        _chk["total_exposure"],
                    )
                    if not allowed:
                        logger.info(
                            "Position limit: skipping %s bin %d — %s",
                            signal.market_id, signal.bin_index, reason,
                        )
                        continue

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
