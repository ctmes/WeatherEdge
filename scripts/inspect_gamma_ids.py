import asyncio
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_markets

async def main():
    target_date = date(2026, 3, 3)
    legacy = await fetch_resolved_markets(target_date, target_date)
    if legacy:
        p, r = legacy[0]
        cid = r.get('conditionId')
        if cid:
            print(f"Gamma conditionId: {cid}")
            print(f"Length (including 0x): {len(cid)}")
            # Also check if it's the same as p.condition_id
            print(f"Parsed condition_id: {p.condition_id}")
            print(f"Length (including 0x): {len(p.condition_id)}")

if __name__ == "__main__":
    asyncio.run(main())
