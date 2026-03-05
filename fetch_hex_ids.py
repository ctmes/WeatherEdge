import asyncio
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_markets, fetch_resolved_daily_events

async def main():
    target_date = date(2026, 3, 3)
    
    # 1. Resolved individual markets
    markets = await fetch_resolved_markets(target_date, target_date)
    print(f"FETCHED_MARKETS|{len(markets)}")
    for p, r in markets:
        cid = r.get('conditionId')
        if cid:
            print(f"MARKET_CID|{cid.lower()}")
        if 'clobTokenIds' in r:
            for tid in r['clobTokenIds']:
                try:
                    # PMXT market_id is often hex(int(clobTokenId))
                    htid = hex(int(tid)).lower()
                    print(f"MARKET_TID_HEX|{htid}")
                except: pass

    # 2. Resolved daily events
    events = await fetch_resolved_daily_events(target_date, target_date)
    print(f"FETCHED_EVENTS|{len(events)}")
    for p, r in events:
        eid = r.get('id')
        if eid:
            print(f"EVENT_ID|{eid.lower()}")
        if 'markets' in r:
            for m in r['markets']:
                mcid = m.get('conditionId')
                if mcid:
                    print(f"CHILD_CID|{mcid.lower()}")
                if 'clobTokenIds' in m:
                    for tid in m['clobTokenIds']:
                        try:
                            htid = hex(int(tid)).lower()
                            print(f"CHILD_TID_HEX|{htid}")
                        except: pass

if __name__ == "__main__":
    asyncio.run(main())
