import asyncio
from datetime import date
import logging
from weatheredge.backtest_fetchers import fetch_resolved_markets, fetch_resolved_daily_events

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO)

async def main():
    target_date = date(2026, 3, 3)
    
    print("--- Fetching Resolved Weather Markets ---")
    markets = await fetch_resolved_markets(target_date, target_date)
    print(f"Found {len(markets)} weather markets.")
    
    for p, r in markets[:3]:
        print(f"\nMarket: {p.question}")
        print(f"  Condition ID: {p.condition_id}")
        print(f"  Token IDs:    {p.token_ids}")
        # print(f"  Raw IDs:      {r.get('id')}, {r.get('conditionId')}")

    print("\n--- Fetching Resolved Daily Events ---")
    events = await fetch_resolved_daily_events(target_date, target_date)
    print(f"Found {len(events)} daily events.")
    
    for p, r in events[:3]:
        print(f"\nEvent: {p.question}")
        print(f"  Condition ID: {p.condition_id}")
        print(f"  Token IDs:    {p.token_ids}")
        # if 'markets' in r:
        #     for m in r['markets']:
        #         print(f"    Child CID: {m.get('conditionId')}, Tokens: {m.get('clobTokenIds')}")

if __name__ == "__main__":
    asyncio.run(main())
