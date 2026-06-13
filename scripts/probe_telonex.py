"""
Probe Telonex coverage — does it have TRADE history for recent weather markets?

If yes, we can run the toxic-fill backtest on historical Telonex data NOW instead
of waiting ~2 weeks for the live recorder. The open question is whether Telonex's
coverage reaches the recent daily-temperature reward markets (its docs mention
2024–2025), so this checks the date range of weather markets it actually has.

READ-ONLY. Uses the TELONEX_API_KEY already in config.

    python -m scripts.probe_telonex
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

import httpx

from weatheredge.config import TELONEX_API_KEY, TELONEX_BASE

log = logging.getLogger("probe_telonex")
_WEATHER = ("temperature", "highest-temp", "lowest-temp", "weather", "celsius", "fahrenheit")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    log.info("Fetching Telonex markets catalog...")
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            resp = await client.get(
                f"{TELONEX_BASE}/datasets/polymarket/markets",
                headers={"Authorization": f"Bearer {TELONEX_API_KEY}"},
            )
            resp.raise_for_status()
            content = resp.content
    except Exception as e:
        log.error("Telonex catalog fetch failed: %s", e)
        log.error("If this is 401/403, the API key is bad/expired; if 402, the plan is out of quota.")
        return

    import pandas as pd

    # Read only the light columns to stay within the droplet's RAM.
    cols = ["slug", "market_id", "asset_id_0", "trades_from"]
    try:
        df = pd.read_parquet(BytesIO(content), columns=cols)
    except Exception:
        df = pd.read_parquet(BytesIO(content))  # fallback: whatever columns exist
    log.info("Catalog columns: %s", list(df.columns))
    log.info("Total markets in catalog: %d", len(df))

    slug_l = df["slug"].astype(str).str.lower()
    mask = slug_l.apply(lambda s: any(w in s for w in _WEATHER))
    weather = df[mask].copy()
    log.info("Weather/temperature markets: %d", len(weather))

    if "trades_from" in weather.columns:
        wt = weather[weather["trades_from"].notna() & (weather["trades_from"].astype(str) != "")]
        log.info("...of which have TRADE data: %d", len(wt))
        if len(wt):
            tf = wt["trades_from"].astype(str)
            log.info("trades_from range: %s  ->  %s", tf.min(), tf.max())
            log.info("\nMost recent 15 weather markets with trades:")
            for _, r in wt.sort_values("trades_from").tail(15).iterrows():
                log.info("  %s | %s", r.get("trades_from"), r.get("slug"))
    else:
        log.info("No 'trades_from' column; raw sample of weather slugs:")
        for s in weather["slug"].head(15):
            log.info("  %s", s)

    log.info(
        "\nVERDICT: if the most-recent trades_from dates reach %s-ish (the current "
        "daily-temp program), Telonex can backtest NOW. If they stop in 2025, it has "
        "the same coverage gap as pmxt and we stick with the live recorder.",
        "2026-05/06",
    )


if __name__ == "__main__":
    asyncio.run(main())
