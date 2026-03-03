"""
Scheduler — runs the WeatherEdge scan-log loop on a fixed interval.

Usage (via CLI):
    python -m weatheredge run              # 30-minute loop, runs forever
    python -m weatheredge run --once       # single scan then exit
    python -m weatheredge run --interval 15  # every 15 minutes
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime

from weatheredge.db import init_db
from weatheredge.logger import log_scan
from weatheredge.pipeline import run_scan

logger = logging.getLogger("weatheredge.scheduler")

# =========================================================================
# Public API
# =========================================================================

async def run_loop(
    *,
    interval_minutes: float = 30.0,
    bankroll: float = 10_000.0,
    once: bool = False,
    resolve_every_n: int = 2,
) -> None:
    """
    Main scheduling loop.

    1. Bootstrap database tables.
    2. Run scan → log signals.
    3. Every *resolve_every_n* cycles, check for resolved markets.
    4. Sleep for *interval_minutes*, then repeat.

    Set *once=True* for a single cycle (useful for testing / cron).
    """
    await init_db()
    logger.info(
        "Scheduler started — interval=%s min, bankroll=$%s, once=%s",
        interval_minutes, f"{bankroll:,.0f}", once,
    )

    cycle = 0
    _running = True

    # Graceful shutdown on SIGINT / SIGTERM
    def _stop(sig, frame):
        nonlocal _running
        logger.info("Received signal %s — shutting down after current cycle.", sig)
        _running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while _running:
        cycle += 1
        logger.info("━━━ Cycle %d ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", cycle)

        # ── Scan + Log ────────────────────────────────────────────
        await _run_one_cycle(bankroll)

        # ── Resolution check ──────────────────────────────────────
        if cycle % resolve_every_n == 0:
            await _run_resolution_check()

        if once:
            logger.info("Single-cycle mode — exiting.")
            break

        # ── Sleep ─────────────────────────────────────────────────
        sleep_secs = interval_minutes * 60
        logger.info("Sleeping %s minutes until next scan...", interval_minutes)
        await asyncio.sleep(sleep_secs)


# =========================================================================
# Internal cycle runners
# =========================================================================

async def _run_one_cycle(bankroll: float) -> None:
    """Execute one full scan and log results."""
    t0 = time.monotonic()
    scan_time = datetime.utcnow()

    try:
        signals, diag = await run_scan(
            bankroll=bankroll, diagnostics=True,
        )
    except Exception:
        logger.exception("Scan FAILED — will retry next cycle.")
        return

    elapsed = time.monotonic() - t0

    # Log to database
    try:
        written = await log_scan(signals, diag, scan_time)
    except Exception:
        logger.exception("Log FAILED — signals not persisted this cycle.")
        return

    # Health check: warn if scan was slow
    if elapsed > 300:  # 5 minutes
        logger.warning("Scan took %.0f seconds — this is unusually slow.", elapsed)

    # Summary
    markets_found = diag.weather_markets_found if diag else "?"
    logger.info(
        "Cycle complete — %d signals logged, %s markets found, %.1f s elapsed.",
        written, markets_found, elapsed,
    )


async def _run_resolution_check() -> None:
    """Attempt to resolve any outstanding paper trades."""
    try:
        from weatheredge.resolver import check_resolutions
        resolved = await check_resolutions()
        if resolved:
            logger.info("Resolved %d signals.", resolved)
    except Exception:
        logger.exception("Resolution check failed — will retry next cycle.")
