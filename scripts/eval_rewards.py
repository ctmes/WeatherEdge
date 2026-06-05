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
    Extract (daily_rate_usd, min_size, max_spread) from a market's rewards block.

    ``daily_rate`` is 0.0 when the market is reward-*eligible* (has min_size /
    max_spread configured) but ``rates`` is null/empty — i.e. no pool is being
    paid right now.  Returns None only when there is no rewards block at all.
    """
    rw = market.get("rewards")
    if not isinstance(rw, dict):
        return None

    # daily rate may live under several shapes; rates can also be None/empty.
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
    if not daily_rate:
        for key in ("rewards_daily_rate", "rewardsDailyRate", "daily_rate"):
            if rw.get(key) is not None:
                daily_rate = float(rw[key])
                break

    min_size = rw.get("min_size") or rw.get("minSize") or rw.get("min_order_size")
    max_spread = rw.get("max_spread") or rw.get("maxSpread")

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
    rows: list[dict[str, Any]] = []
    inactive = 0     # reward-eligible but paying $0 right now (rates: None/empty)
    no_block = 0     # no rewards block at all
    total_pool = 0.0

    for m in weather:
        parsed = _parse_rewards(m)
        q = (m.get("question") or m.get("market_slug") or "?")[:50]
        if parsed is None:
            no_block += 1
            continue
        rate = parsed["daily_rate"]
        if rate <= 0:
            inactive += 1
            continue
        total_pool += rate
        rows.append({
            "rate": rate,
            "req_share": daily_target / rate * 100.0,
            "min_size": parsed["min_size"],
            "max_spread": parsed["max_spread"],
            "q": q,
        })

    # None-safe sort: rank by daily rate only.
    rows.sort(key=lambda r: r["rate"], reverse=True)

    log.info(
        "Eligible weather markets: %d  |  ACTIVELY paying a rate: %d  |  "
        "eligible but $0 (rates:null): %d  |  no rewards block: %d\n",
        len(weather), len(rows), inactive, no_block,
    )

    if not rows:
        log.info(
            "VERDICT: zero weather markets are currently paying a liquidity-reward "
            "rate. Market-making rewards on weather is NOT an income path right now — "
            "every eligible market shows rates:null (a $0 pool).\n"
            "Re-run on another day to confirm it's not just a quiet window."
        )
        return

    log.info("%-52s %9s %9s %9s %10s", "MARKET", "POOL/day", "min_size", "max_spr", "share->tgt")
    log.info("-" * 95)
    for r in rows:
        log.info(
            "%-52s %8.2f$ %9s %9s %9.1f%%",
            r["q"], r["rate"],
            f"{r['min_size']:.0f}" if r["min_size"] is not None else "?",
            f"{r['max_spread']:.2f}" if r["max_spread"] is not None else "?",
            r["req_share"],
        )

    log.info("-" * 95)
    log.info(
        "\nTotal weather reward pool (active only): $%.2f/day  ($%.0f/mo) across %d markets",
        total_pool, total_pool * 30, len(rows),
    )
    pool_share_needed = daily_target / total_pool * 100.0
    log.info(
        "To earn $%.0f/mo you must capture %.1f%% of the ENTIRE active weather "
        "reward pool, every day, across all these markets.",
        monthly_target, pool_share_needed,
    )
    log.info(
        "Your $%.0f bankroll can rest at most ~$%.0f of qualifying size at once "
        "(split two-sided) — eyeball whether that realistically wins the share above\n"
        "against makers quoting far larger size.",
        bankroll, bankroll,
    )
    if no_block:
        log.info("\n(%d markets had no rewards block — schema may have shifted; tell me if that's most of them.)", no_block)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Evaluate Polymarket liquidity-reward economics for weather markets.")
    ap.add_argument("--bankroll", type=float, default=1000.0, help="Working capital in USD (default 1000)")
    ap.add_argument("--monthly-target", type=float, default=300.0, help="Income target in USD/month (default 300)")
    args = ap.parse_args()
    asyncio.run(main(args.bankroll, args.monthly_target))
