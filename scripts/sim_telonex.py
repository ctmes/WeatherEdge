"""
Toxic-fill backtest on Telonex historical TRADE data — the real go/no-go,
runnable NOW (no waiting for the live recorder).

For a sample of already-resolved weather markets it:
  1. pulls the tick-level trade-price path from Telonex,
  2. infers the YES/NO outcome from where the price pinned at the end,
  3. runs the validated passive-maker sim (fill = a trade printed through your
     resting quote = an adverse move), scoring adverse-selection cost, and
  4. aggregates across markets and reports net vs. an assumed reward.

READ-ONLY. Uses TELONEX_API_KEY from config. Telonex is metered, so each
(slug, day) is fetched once and disk-cached; start with a modest --n.

Run it locally (more RAM than the droplet, and it's just an API):
    python -m scripts.sim_telonex --n 40 --size 50 --band 0.045 --reward-per-day 50 --share 0.10
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import httpx

from weatheredge.config import TELONEX_API_KEY, TELONEX_BASE
from weatheredge.telonex_fetcher import _fetch_day_trades
from scripts.sim_maker import simulate_passive_maker

log = logging.getLogger("sim_telonex")

_WEATHER = ("temperature", "highest-temp", "lowest-temp")
_DATE_RE = re.compile(r"on-([a-z]+)-(\d{1,2})-(\d{4})")


async def _fetch_catalog():
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as c:
        r = await c.get(
            f"{TELONEX_BASE}/datasets/polymarket/markets",
            headers={"Authorization": f"Bearer {TELONEX_API_KEY}"},
        )
        r.raise_for_status()
        content = r.content
    import pandas as pd
    return pd.read_parquet(BytesIO(content), columns=["slug", "trades_from"])


def _slug_date(slug: str) -> date | None:
    m = _DATE_RE.search(slug)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
    except ValueError:
        return None


async def _path_for(slug: str, res_date: date) -> tuple[list[tuple[int, float]], bool]:
    """Return (price_path, quota_hit). Path is [(ts_ms, price)] over the market's last ~2 days."""
    rows: list[dict] = []
    for day in [(res_date - timedelta(days=1)).isoformat(), res_date.isoformat()]:
        t = await _fetch_day_trades(slug, day)
        if t is None:
            return [], True  # 402 quota / error
        rows.extend(t)
    rows.sort(key=lambda x: x["timestamp_us"])
    path = [
        (int(r["timestamp_us"] // 1000), float(r["price"]))
        for r in rows
        if r.get("price") is not None and 0.0 <= float(r["price"]) <= 1.0
    ]
    return path, False


async def main(args) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    log.info("Fetching Telonex catalog...")
    df = await _fetch_catalog()
    today = datetime.now(timezone.utc).date()

    # Resolved weather markets (date in slug already past), most recent first.
    cand: list[tuple[date, str]] = []
    for slug in df["slug"].astype(str):
        s = slug.lower()
        if not any(w in s for w in _WEATHER):
            continue
        d = _slug_date(s)
        if d is None or d >= today:
            continue
        cand.append((d, slug))
    cand.sort(reverse=True)
    sample = cand[: args.n]
    log.info("Backtesting %d resolved weather markets (of %d candidates)\n", len(sample), len(cand))

    log.info("%-56s %3s %5s %9s %9s", "MARKET", "res", "fills", "adverse$", "resol$")
    log.info("-" * 90)

    agg_adv = agg_res = 0.0
    fills_tot = counted = yes_cnt = 0
    quota_hit = False

    for d, slug in sample:
        path, quota_hit = await _path_for(slug, d)
        if quota_hit:
            log.warning("\nTelonex quota/error hit — stopping early with %d markets done.", counted)
            break
        if len(path) < 2:
            continue
        resolution = 1 if path[-1][1] >= 0.5 else 0
        r = simulate_passive_maker(path, args.band, args.size, float(resolution))
        agg_adv += r["adverse_pnl"]
        agg_res += r["resolution_pnl"]
        fills_tot += r["fills"]
        yes_cnt += resolution
        counted += 1
        log.info("%-56s %3d %5d %+8.2f$ %+8.2f$",
                 slug[:56], resolution, r["fills"], r["adverse_pnl"], r["resolution_pnl"])

    if not counted:
        log.info("\nNo markets backtested (no trade paths). Try a larger --n or check Telonex quota.")
        return

    # Reward over a market's ~1-day life at your share.
    reward_per_market = args.reward_per_day * args.share
    reward_tot = reward_per_market * counted
    net = reward_tot + agg_adv  # adverse is negative

    log.info("-" * 90)
    log.info("\nBacktested %d markets (%d resolved YES, %d NO) at $%.0f/side, band %.3f:",
             counted, yes_cnt, counted - yes_cnt, args.size, args.band)
    log.info("  total fills:                 %d", fills_tot)
    log.info("  total adverse-selection:     %+9.2f$   (toxicity — the cost of being a passive maker)", agg_adv)
    log.info("  total hold-to-resolution:    %+9.2f$", agg_res)
    log.info("  avg adverse / market:        %+9.2f$", agg_adv / counted)
    log.info("  assumed reward / market:     %+9.2f$   ($%.0f/day pool × %.0f%% share)",
             reward_per_market, args.reward_per_day, args.share * 100)
    log.info("  " + "-" * 50)
    log.info("  NET / market (reward+adverse): %+8.2f$", net / counted)
    log.info("  NET total:                     %+8.2f$", net)
    # What reward share would break even on toxicity alone?
    if agg_adv < 0 and args.reward_per_day > 0:
        breakeven_share = (-agg_adv / counted) / args.reward_per_day * 100
        log.info("\n  Break-even: you'd need ≥ %.1f%% reward share to offset toxicity at this size.", breakeven_share)

    log.info(
        "\n%s",
        "→ NET POSITIVE: toxicity is covered by plausible rewards — worth a real look."
        if net > 0 else
        "→ NET NEGATIVE: toxic fills exceed the assumed reward. Naive passive MM loses here;\n"
        "  the only path to positive is your weather model cutting the adverse fills (skew/pull\n"
        "  quotes). Re-run with a model-gated rule to test that.",
    )
    log.info(
        "\nNotes: trade-data fills are real prints (good), but no depth → reward SHARE is assumed,\n"
        "not measured (the live recorder gives depth for that). Outcome inferred from final price."
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Toxic-fill backtest on Telonex historical trade data.")
    ap.add_argument("--n", type=int, default=40, help="How many recent resolved weather markets to test")
    ap.add_argument("--size", type=float, default=50.0, help="USD rested per side")
    ap.add_argument("--band", type=float, default=0.045, help="Quote offset from mid (price units; 4.5c default)")
    ap.add_argument("--reward-per-day", type=float, default=50.0, help="Assumed market reward pool $/day")
    ap.add_argument("--share", type=float, default=0.10, help="Assumed reward share fraction")
    asyncio.run(main(ap.parse_args()))
