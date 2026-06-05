"""
Rewards-economics evaluator — answers ONE question before we build any
market-making infrastructure: at a small bankroll, is farming Polymarket's
liquidity-rewards program on weather markets actually worth it?

It is strictly READ-ONLY. It places no orders and needs no API key/wallet.

What it does
------------
1. Pulls the markets Polymarket is currently paying liquidity rewards on
   (CLOB ``/sampling-markets`` returns reward-eligible markets only).
2. Filters to weather / temperature markets.
3. For each, prints the reward parameters (daily rate, min order size,
   max qualifying spread) and the current top-of-book spread.
4. Computes the decision number: to hit a monthly income target, what
   fraction of each market's daily reward pool would you have to capture?
   That share is something you can sanity-check by eye — if you'd need to
   win 60% of a pool that bigger makers are also farming, MM is not your edge.

Run it ON THE VPS (Singapore reaches Polymarket):
    python -m scripts.eval_rewards            # defaults: $1000 bankroll, $300/mo target
    python -m scripts.eval_rewards --bankroll 1000 --monthly-target 300

NOTE ON SCHEMA: the CLOB rewards block field names have shifted across API
versions. This script tries the known variants and, if it can't find a rate,
prints the raw ``rewards`` JSON so you can paste it back and we refine the parser.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

from weatheredge.config import POLYMARKET_CLOB_BASE, WEATHER_KEYWORDS, NON_WEATHER_BLOCKLIST
from weatheredge.polymarket import _get_json

log = logging.getLogger("eval_rewards")


# ---------------------------------------------------------------------------
# Fetch reward-eligible markets
# ---------------------------------------------------------------------------

async def _fetch_sampling_markets(max_pages: int = 20) -> list[dict[str, Any]]:
    """Page through CLOB /sampling-markets (reward-eligible markets only)."""
    markets: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(max_pages):
        params = {"next_cursor": cursor} if cursor else {}
        try:
            data = await _get_json(f"{POLYMARKET_CLOB_BASE}/sampling-markets", params)
        except Exception as e:
            log.warning("sampling-markets fetch failed (cursor=%r): %s", cursor, e)
            break
        if not isinstance(data, dict):
            break
        batch = data.get("data", [])
        markets.extend(batch)
        cursor = data.get("next_cursor", "") or ""
        if not cursor or cursor == "LTE=" or not batch:  # "LTE=" = end sentinel
            break
    return markets


def _is_weather(market: dict[str, Any]) -> bool:
    text = " ".join(
        str(market.get(k, "")) for k in ("question", "description", "market_slug")
    ).lower()
    if any(bl in text for bl in NON_WEATHER_BLOCKLIST):
        return False
    return any(kw in text for kw in WEATHER_KEYWORDS)


# ---------------------------------------------------------------------------
# Reward-block parsing (tolerant of schema drift)
# ---------------------------------------------------------------------------

def _parse_rewards(market: dict[str, Any]) -> dict[str, Any] | None:
    """
    Extract (daily_rate_usd, min_size, max_spread) from a market's rewards block,
    tolerating the field-name variants seen across CLOB API versions.
    Returns None (and the caller prints the raw block) if no rate is found.
    """
    rw = market.get("rewards") or {}
    if not isinstance(rw, dict):
        return None

    # daily rate may live under several shapes
    daily_rate = 0.0
    rates = rw.get("rates")
    if isinstance(rates, list):
        for r in rates:
            if not isinstance(r, dict):
                continue
            for key in ("rewards_daily_rate", "rewardsDailyRate", "daily_rate", "rate"):
                if r.get(key) is not None:
                    daily_rate += float(r[key])
                    break
    for key in ("rewards_daily_rate", "rewardsDailyRate", "daily_rate"):
        if not daily_rate and rw.get(key) is not None:
            daily_rate = float(rw[key])
            break

    min_size = rw.get("min_size") or rw.get("minSize") or rw.get("min_order_size")
    max_spread = rw.get("max_spread") or rw.get("maxSpread")

    if daily_rate <= 0:
        return None
    return {
        "daily_rate": daily_rate,
        "min_size": float(min_size) if min_size is not None else None,
        "max_spread": float(max_spread) if max_spread is not None else None,
        "raw": rw,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(bankroll: float, monthly_target: float) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    markets = await _fetch_sampling_markets()
    log.info("Reward-eligible markets returned by CLOB: %d", len(markets))

    weather = [m for m in markets if _is_weather(m)]
    log.info("Weather/temperature markets among them: %d\n", len(weather))

    if not weather:
        log.info(
            "No reward-eligible WEATHER markets right now. Either none are in the "
            "rewards program currently, or the sampling endpoint/schema changed.\n"
            "Re-run later, or paste me the raw output if you expected some."
        )
        # Still show whether ANY rewards exist so we know the endpoint works.
        log.info("(For reference, %d non-weather reward markets exist.)", len(markets) - len(weather))
        return

    daily_target = monthly_target / 30.0
    rows: list[tuple] = []
    total_pool = 0.0
    unparsed = 0

    for m in weather:
        parsed = _parse_rewards(m)
        q = (m.get("question") or m.get("market_slug") or "?")[:50]
        if parsed is None:
            unparsed += 1
            log.info("  [no parseable rate] %s\n    raw rewards: %s", q, m.get("rewards"))
            continue
        rate = parsed["daily_rate"]
        total_pool += rate
        # Required share of THIS market's pool to hit the whole daily target alone.
        req_share = (daily_target / rate * 100.0) if rate > 0 else float("inf")
        rows.append((rate, req_share, parsed["min_size"], parsed["max_spread"], q))

    rows.sort(reverse=True)

    log.info("%-52s %9s %9s %9s %10s", "MARKET", "POOL/day", "min_size", "max_spr", "share→tgt")
    log.info("-" * 95)
    for rate, req_share, min_size, max_spread, q in rows:
        log.info(
            "%-52s %8.2f$ %9s %9s %9.1f%%",
            q, rate,
            f"{min_size:.0f}" if min_size is not None else "?",
            f"{max_spread:.2f}" if max_spread is not None else "?",
            req_share,
        )

    log.info("-" * 95)
    log.info(
        "\nTotal weather reward pool: $%.2f/day  ($%.0f/mo) across %d markets",
        total_pool, total_pool * 30, len(rows),
    )
    if total_pool > 0:
        pool_share_needed = daily_target / total_pool * 100.0
        log.info(
            "To earn $%.0f/mo from rewards you must capture %.1f%% of the ENTIRE "
            "weather reward pool (every day, across all these markets).",
            monthly_target, pool_share_needed,
        )
        log.info(
            "Your $%.0f bankroll can rest at most ~$%.0f of qualifying size at once, "
            "split two-sided across markets — eyeball whether that wins the share above.",
            bankroll, bankroll,
        )
    if unparsed:
        log.info("\n(%d markets had a rewards block I couldn't parse — raw JSON printed above.)", unparsed)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Evaluate Polymarket liquidity-reward economics for weather markets.")
    ap.add_argument("--bankroll", type=float, default=1000.0, help="Working capital in USD (default 1000)")
    ap.add_argument("--monthly-target", type=float, default=300.0, help="Income target in USD/month (default 300)")
    args = ap.parse_args()
    asyncio.run(main(args.bankroll, args.monthly_target))
