import asyncio
import json
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_daily_events

async def audit_gamma_details():
    target_date = date(2026, 3, 3)
    events = await fetch_resolved_daily_events(target_date, target_date)
    if not events: return
    
    # Pick NYC or similar if found
    target = None
    for p, r in events:
        if "NYC" in p.question:
            target = (p, r)
            break
    if not target: target = events[0]
    
    p, r = target
    print(f"Market: {p.question}")
    print(f"Parent Event ID: {r.get('id')}")
    
    if 'markets' in r:
        for i, m in enumerate(r['markets']):
            print(f"\nBin {i}: {m.get('groupItemTitle')}")
            print(f"  Condition ID: {m.get('conditionId')}")
            tids = m.get('clobTokenIds', [])
            print(f"  clobTokenIds: {json.dumps(tids)}")
            print(f"  market_id (slug): {m.get('market_id')}")

if __name__ == "__main__":
    asyncio.run(audit_gamma_details())
