import asyncio
from datetime import date
from weatheredge.pmxt_fetcher import _normalize_id, _download_parquet
from weatheredge.backtest_fetchers import fetch_resolved_daily_events

async def test():
    # Test normalization
    val = "7176461427909384724021204859873461461972049080516629731295988583416954200000"
    norm = _normalize_id(val)
    print(f"Decimal: {val}")
    print(f"Normalized: {norm}")
    
    # Check against Parquet
    url = "https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet"
    print(f"Downloading {url}...")
    df = _download_parquet(url)
    mids = df["market_id"].unique().to_list()
    
    # Fetch real data from Gamma to get a real ID
    print("Fetching real IDs from Gamma...")
    events = await fetch_resolved_daily_events(date(2026, 3, 3), date(2026, 3, 3))
    if events:
        _, r = events[0]
        print(f"Checking event: {r.get('title')}")
        for m in r.get('markets', []):
            desc = m.get('groupItemTitle', '')
            tids_raw = m.get('clobTokenIds')
            
            tids = []
            if isinstance(tids_raw, str) and tids_raw.startswith('['):
                import json
                try: tids = json.loads(tids_raw)
                except: pass
            elif isinstance(tids_raw, list):
                tids = [str(t) for t in tids_raw]
            
            for i, tid in enumerate(tids):
                side = "YES" if i == 0 else "NO"
                norm_tid = _normalize_id(tid)
                if norm_tid in mids:
                    print(f"SUCCESS: Match found! {desc} ({side}): {tid} -> {norm_tid}")
                else:
                    # Check if normalized CID matches
                    cid = m.get('conditionId')
                    norm_cid = _normalize_id(cid)
                    if norm_cid in mids:
                         print(f"SUCCESS: Match found for CID! {desc}: {cid} -> {norm_cid}")

    print("Total PMXT market_ids inspected:", len(mids))
    print("Sample PMXT IDs:", mids[:3])

if __name__ == "__main__":
    asyncio.run(test())
