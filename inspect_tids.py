import asyncio
import json
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_daily_events

async def main():
    target_date = date(2026, 3, 3)
    events = await fetch_resolved_daily_events(target_date, target_date)
    if not events:
        print("No events")
        return
    
    p, r = events[0]
    print(f"Goal: {p.question}")
    if 'markets' in r:
        for m in r['markets']:
            print(f"\nBin: {m.get('groupItemTitle')}")
            tids = m.get('clobTokenIds')
            print(f"  clobTokenIds: {tids}")
            if tids:
                for tid in tids:
                    print(f"    Type: {type(tid)}")
                    print(f"    Value: {tid}")

if __name__ == "__main__":
    asyncio.run(main())
