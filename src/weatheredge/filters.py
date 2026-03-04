"""
Anti-manipulation filters — detecting spoofing and thin markets.
From The Adversary persona's procedural advice.
"""

from __future__ import annotations
import logging

logger = logging.getLogger("weatheredge.filters")

def check_midpoint_integrity(
    bids: list[list[float]] | None,
    asks: list[list[float]] | None,
    spread_threshold: float = 0.10,
    skew_threshold: float = 5.0
) -> dict:
    """
    Check if the market midpoint is reliable or potentially manipulated.
    
    bids/asks: list of [price, size]
    spread_threshold: max allowed spread as fraction of midpoint (0.10 = 10%)
    skew_threshold: max allowed ratio between bid/ask volume at top of book
    
    Returns a dict with 'is_spooky', 'reason', and 'multiplier'.
    """
    if not bids or not asks:
        return {"is_spooky": True, "reason": "empty_book", "multiplier": 0.0}
        
    try:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        bid_size = float(bids[0][1])
        ask_size = float(asks[0][1])
    except (IndexError, ValueError, TypeError):
        return {"is_spooky": True, "reason": "invalid_book_data", "multiplier": 0.0}
    
    midpoint = (best_bid + best_ask) / 2.0
    spread = (best_ask - best_bid) / midpoint if midpoint > 0 else float('inf')
    
    # 1. Wide Spread check
    if spread > spread_threshold:
        return {
            "is_spooky": True, 
            "reason": f"wide_spread ({spread:.2%})", 
            "multiplier": 0.5 # reduce sizing for illiquid markets
        }
    
    # 2. Volume Skewness (potential spoofing)
    # If one side has massive size and the other has tiny size at the top,
    # it might be a spoofed price.
    min_size = min(bid_size, ask_size)
    skew = max(bid_size, ask_size) / min_size if min_size > 0 else 100.0
    
    if skew > skew_threshold:
        return {
            "is_spooky": True,
            "reason": f"volume_skew ({skew:.1f}x)",
            "multiplier": 0.2
        }
        
    return {"is_spooky": False, "reason": None, "multiplier": 1.0}
