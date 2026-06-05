"""
Rewards-economics evaluator — answers the ONLY question that matters before we
build any market-making code: at a $1k bankroll, what is your *realistic* share
of Polymarket's weather liquidity-reward pools, and what does that pay per month?

Strictly READ-ONLY. Places no orders, needs no API key/wallet.

Two stages
----------
STAGE 1 (pool size):   CLOB /sampling-markets lists reward-eligible markets and
                       their daily reward pool. We filter to weather/temperature.
STAGE 2 (your share):  For the fattest pools we pull the LIVE order book and
                       measure how much *competing* maker liquidity is already
                       resting within the reward spread band of the midpoint.
                       Your share ≈ your_size / (your_size + competing_size),
                       which turns the pool into a realistic $/day and $/month.

The share model is a deliberate first-order proxy. Polymarket's true reward
formula also weights proximity-to-mid and time, and requires two-sided quoting —
so treat the output as an UPPER-ish bound on income and a sanity check, not a
guarantee. It does NOT model toxic-fill losses (informed traders picking off
your resting orders), which are the other half of net P&L.

Run on the VPS (Singapore reaches Polymarket):
    python -m scripts.eval_rewards
    python -m scripts.eval_rewards --bankroll 1000 --monthly-target 300 \
                                   --per-market 50 --top 25 --spread-units cents
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from typing import Any

from weatheredge.config import POLYMARKET_CLOB_BASE, NON_WEATHER_BLOCKLIST
from weatheredge.polymarket import _get_json

log = logging.getLogger("eval_rewards")

# Tight weather match on the QUESTION text only (description/slug leaked
# false-positives like "Rainbow Six" via the substring "rain"). Whole-word
# weather terms + degree symbols.
_WEATHER_RE = re.compile(
    r"(°\s*[cf]\b|\b(temperature|celsius|fahrenheit|rainfall|snowfall|"
    r"precipitation|hurricane|tornado|cyclone|blizzard|heatwave|sea ice|"
    r"hottest|coldest|frost)\b)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Stage 1: reward-eligible markets
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
        if not cursor or cursor == "LTE=" or not batch:
            break
    return markets


def _is_weather(market: dict[str, Any]) -> bool:
    q = str(market.get("question", ""))
    if any(bl in q.lower() for bl in NON_WEATHER_BLOCKLIST):
        return False
    return bool(_WEATHER_RE.search(q))


def _parse_rewards(market: dict[str, Any]) -> dict[str, Any] | None:
    """Extract daily_rate (0.0 if rates null/empty), min_size, max_spread."""
    rw = market.get("rewards")
    if not isinstance(rw, dict):
        return None
    daily_rate = 0.0
    rates = rw.get("rates")
    if isinstance(rates, list):
        for r in rates:
            if isinstance(r, dict):
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
    }


def _first_token_id(market: dict[str, Any]) -> str | None:
    toks = market.get("tokens") or []
    for t in toks:
        tid = str(t.get("token_id", "")).strip() if isinstance(t, dict) else ""
        if tid:
            return tid
    return None


# ---------------------------------------------------------------------------
# Stage 2: live order book → competing in-band liquidity
# ---------------------------------------------------------------------------

async def _fetch_book(token_id: str) -> dict[str, Any] | None:
    try:
        return await _get_json(f"{POLYMARKET_CLOB_BASE}/book", {"token_id": token_id})
    except Exception as e:
        log.debug("book fetch failed for %s: %s", token_id, e)
        return None


def _competing_in_band_usd(book: dict[str, Any], band: float) -> tuple[float, float] | None:
    """
    Return (mid, competing_usd) — total resting maker notional within `band`
    (price units) of the midpoint, summed over both bid and ask sides.
    """
    def _levels(side):
        out = []
        for lvl in book.get(side, []) or []:
            try:
                out.append((float(lvl["price"]), float(lvl["size"])))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    bids = sorted(_levels("bids"), key=lambda x: x[0], reverse=True)
    asks = sorted(_levels("asks"), key=lambda x: x[0])
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2.0

    usd = 0.0
    for price, size in bids:
        if price >= mid - band:
            usd += price * size
    for price, size in asks:
        if price <= mid + band:
            usd += price * size
    return mid, usd


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    sampling = await _fetch_sampling_markets()
    weather = [m for m in sampling if _is_weather(m)]
    log.info("Reward-eligible markets: %d  |  weather (tight filter): %d",
             len(sampling), len(weather))

    active: list[dict[str, Any]] = []
    for m in weather:
        p = _parse_rewards(m)
        if not p or p["daily_rate"] <= 0:
            continue
        tid = _first_token_id(m)
        if not tid:
            continue
        active.append({
            "q": (m.get("question") or "?")[:46],
            "rate": p["daily_rate"],
            "min_size": p["min_size"],
            "max_spread": p["max_spread"],
            "token_id": tid,
        })

    active.sort(key=lambda r: r["rate"], reverse=True)
    total_pool = sum(r["rate"] for r in active)
    log.info("Active weather pools: %d markets, $%.0f/day ($%.0f/mo) total\n",
             len(active), total_pool, total_pool * 30)

    top = active[: args.top]
    your_usd = args.per_market * 2.0  # two-sided
    spread_div = 100.0 if args.spread_units == "cents" else 1.0

    log.info("Stage 2 — measuring competing liquidity in the reward band for top %d pools", len(top))
    log.info("(your stake per market: $%.0f two-sided)\n", your_usd)
    log.info("%-46s %8s %9s %8s %9s %9s", "MARKET", "POOL/d", "compet$", "share%", "you/day", "you/mo")
    log.info("-" * 95)

    results = []
    for r in top:
        band = (r["max_spread"] or 0.0) / spread_div
        book = await _fetch_book(r["token_id"])
        if not book or band <= 0:
            log.info("%-46s %7.0f$ %9s %8s %9s %9s", r["q"], r["rate"], "no book", "-", "-", "-")
            continue
        comp = _competing_in_band_usd(book, band)
        if comp is None:
            log.info("%-46s %7.0f$ %9s %8s %9s %9s", r["q"], r["rate"], "empty", "-", "-", "-")
            continue
        _mid, competing_usd = comp
        share = your_usd / (competing_usd + your_usd)
        you_day = share * r["rate"]
        results.append(you_day)
        log.info(
            "%-46s %7.0f$ %8.0f$ %7.1f%% %8.2f$ %8.2f$",
            r["q"], r["rate"], competing_usd, share * 100, you_day, you_day * 30,
        )

    log.info("-" * 95)

    # Realistic total: you can only fund floor(bankroll / your_usd) markets at once.
    fundable = max(1, int(args.bankroll // your_usd)) if your_usd > 0 else 0
    results.sort(reverse=True)
    realistic_day = sum(results[:fundable])
    log.info(
        "\nYour $%.0f funds ~%d markets two-sided at $%.0f each.",
        args.bankroll, fundable, your_usd,
    )
    log.info(
        "Realistic reward income (best %d of the measured pools): $%.2f/day  ->  $%.0f/month",
        fundable, realistic_day, realistic_day * 30,
    )
    log.info(
        "\nReality checks NOT in this number:\n"
        "  - This is gross rewards; subtract losses to toxic fills (informed flow).\n"
        "  - Assumes you actually hold qualifying two-sided quotes ~100%% of the time.\n"
        "  - Share model ignores Polymarket's proximity/time weighting (optimistic)."
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Evaluate Polymarket weather liquidity-reward economics + realistic share.")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--monthly-target", type=float, default=300.0)
    ap.add_argument("--per-market", type=float, default=50.0, help="USD rested per side, per market")
    ap.add_argument("--top", type=int, default=25, help="How many of the fattest pools to measure books for")
    ap.add_argument("--spread-units", choices=["cents", "price"], default="cents",
                    help="Units of max_spread from the API (4.5 → 0.045 if cents)")
    asyncio.run(main(ap.parse_args()))
