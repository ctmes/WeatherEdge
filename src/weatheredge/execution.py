"""
Execution Module - Handles placing trades on Polymarket.
Supports both Paper (logging) and Live (API) modes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from weatheredge.config import TRADING
from weatheredge.models import EdgeSignal
from weatheredge.logger import log_scan

logger = logging.getLogger("weatheredge.execution")

# ---------------------------------------------------------------------------
# In-memory deduplication cache
#
# Seeded from the DB on first use via load_open_positions().  Thereafter,
# every confirmed execution adds to this set so within-cycle duplicates are
# blocked without a DB round-trip.  Because it is seeded from DB it also
# survives process restarts and VPS reboots — re-deploying will not cause
# the system to re-fire every previously placed signal.
# ---------------------------------------------------------------------------

_executed_signals: set[tuple[str, int]] = set()
_positions_loaded: bool = False


def _signal_key(signal: EdgeSignal) -> tuple[str, int]:
    return (str(signal.market_id), int(signal.bin_index))


async def load_open_positions() -> None:
    """
    Seed ``_executed_signals`` from the database on startup.

    Loads all (market_id, bin_index) pairs for signals that have been
    placed but not yet resolved (no Result row).  Call this once during
    daemon startup before the first evaluation cycle.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _executed_signals, _positions_loaded
    if _positions_loaded:
        return

    from weatheredge.db import get_open_signal_keys, get_session

    async with get_session() as session:
        keys = await get_open_signal_keys(session)

    _executed_signals.update(keys)
    _positions_loaded = True
    logger.info(
        "Loaded %d open position(s) from DB into execution deduplication cache.",
        len(keys),
    )


def clear_executed_signals() -> None:
    """
    Clear the in-memory deduplication set and reset the loaded flag.

    Call this after a market refresh so that closed/resolved markets
    can be re-evaluated.  The DB remains the source of truth — the set
    will be re-seeded from DB on the next call to ``load_open_positions``.
    """
    global _executed_signals, _positions_loaded
    _executed_signals.clear()
    _positions_loaded = False
    logger.info("Execution deduplication cache cleared; will reload from DB on next cycle.")


async def execute_signal(
    signal: EdgeSignal,
    bankroll: float,
    live: bool = False,
) -> bool:
    """
    Execute a single trade signal.

    Deduplication order:
      1. In-memory cache check (fast, no DB round-trip)
      2. DB check via ``has_open_position`` (catches positions from prior
         process runs that weren't loaded into memory yet)

    If either check finds an existing open position the signal is skipped.
    This means the same (market_id, bin_index) pair will never be executed
    twice while the position remains unresolved, even across restarts.
    """
    await load_open_positions()

    key = _signal_key(signal)

    # 1. Fast in-memory check
    if key in _executed_signals:
        logger.debug(
            "Skipping duplicate signal: market %s bin %d already in execution cache.",
            signal.market_id, signal.bin_index,
        )
        return False

    # 2. DB check — catches positions not yet in the in-memory cache
    from weatheredge.db import get_session, has_open_position
    async with get_session() as session:
        already_open = await has_open_position(session, signal.market_id, signal.bin_index)

    if already_open:
        logger.info(
            "Skipping signal: open position already exists in DB for market %s bin %d.",
            signal.market_id, signal.bin_index,
        )
        _executed_signals.add(key)
        return False

    # 3. Minimum bet guard
    if signal.recommended_bet_usd <= 0:
        logger.debug(
            "Skipping signal: market %s bin %d has zero bet size.",
            signal.market_id, signal.bin_index,
        )
        return False

    mode_str = "LIVE" if live else "PAPER"
    logger.info(
        "[%s] Executing signal: %s bin %d (%s)",
        mode_str, signal.market_id, signal.bin_index, signal.bin_label,
    )
    logger.info(
        "   Bet size: $%.2f (Kelly: %.3f, Edge: %.1f%%)",
        signal.recommended_bet_usd, signal.kelly_fraction, signal.net_edge * 100,
    )

    if live:
        if not TRADING.live_trading_enabled:
            logger.warning("Execution rejected: live_trading_enabled is False in config.")
            return False

        # Live path: does NOT write to the paper DB.
        # TODO: place real CLOB order via API client here.
        logger.warning(
            "LIVE execution for %s is not yet implemented. Signal will NOT be recorded.",
            signal.market_id,
        )
        _executed_signals.add(key)
        return False

    success = await _record_paper_trade(signal)
    if success:
        # Only add to cache after confirmed DB write so a failed write
        # doesn't permanently suppress this signal.
        _executed_signals.add(key)
    return success


async def _record_paper_trade(signal: EdgeSignal) -> bool:
    """
    Record a paper trade to the database.

    Passes an empty diagnostics dict instead of None so log_scan does not
    need to defensively handle a None argument.

    Re-raises unexpected DB exceptions after logging so the caller is
    aware the write failed — a False return unambiguously means failure.
    """
    try:
        await log_scan([signal], {}, datetime.now(tz=timezone.utc))
        return True
    except Exception:
        logger.exception(
            "Failed to record paper trade for market %s bin %d",
            signal.market_id, signal.bin_index,
        )
        raise