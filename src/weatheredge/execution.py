"""
Execution Module - Handles placing trades on Polymarket.
Supports both Paper (logging) and Live (API) modes.
"""

import logging
from datetime import datetime
from typing import Optional

from weatheredge.config import TRADING
from weatheredge.models import EdgeSignal
from weatheredge.logger import log_scan

logger = logging.getLogger("weatheredge.execution")

async def execute_signal(signal: EdgeSignal, bankroll: float, live: bool = False):
    """
    Execute a single trade signal.
    
    If live=False, this is a Paper Trade. It logs the intent to the database.
    If live=True, this would interact with the Polymarket CLOB API (Future).
    """
    mode_str = "LIVE" if live else "PAPER"
    logger.info(f"[{mode_str}] Executing signal: {signal.market_id} bin {signal.bin_index} ({signal.bin_label})")
    logger.info(f"   Bet size: ${signal.recommended_bet_usd:.2f} (Kelly: {signal.kelly_fraction:.3f}, Edge: {signal.net_edge:.1%})")

    if live:
        # Check global guard again
        if not TRADING.live_trading_enabled:
            logger.warning("Execution rejected: live_trading_enabled is False in config.")
            return False
        
        # TODO: Implement real CLOB order placement here
        logger.warning(f"LIVE execution for {signal.market_id} is not yet fully implemented in API client. Falling back to paper log.")
        # For now, we still just log it, but we mark it as 'intent_live'
        return await _record_paper_trade(signal, source="live_intent")
    else:
        return await _record_paper_trade(signal, source="paper")

async def _record_paper_trade(signal: EdgeSignal, source: str = "paper"):
    """Record a paper trade to the database."""
    # The current log_scan expects a list of signals and a diagnostic object.
    # We can use it to persist the signal as a paper trade.
    try:
        # We wrap it in a list because log_scan expects multiples
        # We pass None for diagnostics since this is a single execution event
        await log_scan([signal], None, datetime.utcnow())
        return True
    except Exception as e:
        logger.error(f"Failed to record paper trade: {e}")
        return False
