import asyncio
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_markets

async def main():
    target_date = date(2026, 3, 3)
    markets = await fetch_resolved_markets(target_date, target_date)
    if not markets:
        print("No markets found for 2026-03-03")
        return
    
    # Take the first market
    p, r = markets[0]
    print(f"Market Title: {r.get('title') or r.get('question')}")
    print(f"Condition ID: {r.get('conditionId')}")
    print(f"Question ID: {r.get('questionId')}")
    print(f"Market ID (Gamma): {r.get('id')}")
    
    if "markets" in r:
        print("\nChild Markets:")
        for m in r["markets"]:
            print(f"  - Title: {m.get('groupItemTitle')}")
            print(f"    Condition ID: {m.get('conditionId')}")
            print(f"    Question ID: {m.get('questionId')}")
            print(f"    Market ID: {m.get('id')}")
            print(f"    CLOB Token IDs: {m.get('clobTokenIds')}")

if __name__ == "__main__":
    asyncio.run(main())
