import asyncio
import logging
from datetime import date
from weatheredge.pmxt_fetcher import fetch_pmxt_resolved_markets

logging.basicConfig(level=logging.INFO)

async def main():
    print("Starting fetch...")
    pairs = await fetch_pmxt_resolved_markets(date(2026, 3, 3), date(2026, 3, 3))
    print(f"\n====================\nTotal pairs: {len(pairs)}\n====================")
    
    enriched = 0
    for p, r in pairs:
        ob = r.get('_pmxt_orderbook', {})
        if ob:
            enriched += 1
            if enriched <= 5:
                print(f"Question: {p.question}")
                print(f"Prices: {p.outcome_prices}")
                print(f"Token IDs: {p.token_ids}")
                print(f"PMXT orderbook: {ob}")
                print("-" * 40)
    print(f"Total Enriched: {enriched}")

if __name__ == "__main__":
    asyncio.run(main())
