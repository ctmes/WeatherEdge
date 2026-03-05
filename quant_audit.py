import asyncio
import json
import polars as pl
from datetime import date, datetime, timedelta
from weatheredge.backtest_fetchers import fetch_resolved_daily_events
from weatheredge.pmxt_fetcher import _download_parquet, _normalize_id

async def quant_audit():
    target_date = date(2026, 3, 3)
    print(f"--- Quant Audit for {target_date} ---")
    
    # 1. Fetch from Gamma
    print("Step 1: Fetching Gamma events...")
    events = await fetch_resolved_daily_events(target_date, target_date)
    if not events:
        print("No Gamma events found.")
        return
        
    p, r = events[0]
    print(f"Sample Question: {p.question}")
    
    gamma_tokens = []
    if 'markets' in r:
        for m in r['markets']:
            tids_raw = m.get('clobTokenIds')
            
            tids = []
            if isinstance(tids_raw, str) and tids_raw.startswith('['):
                import json
                try: tids = json.loads(tids_raw)
                except: pass
            elif isinstance(tids_raw, str):
                tids = [t.strip() for t in tids_raw.split(',') if t.strip()]
            elif isinstance(tids_raw, list):
                tids = [str(t) for t in tids_raw]
            
            if tids:
                gamma_tokens.append(tids[0]) # YES token
    
    print(f"Collected {len(gamma_tokens)} token IDs from sample event.")
    
    # 2. Inspect Parquet
    url = f"https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet"
    print(f"\nStep 2: Inspecting Parquet {url}...")
    df = _download_parquet(url)
    mids = df["market_id"].unique().to_list()
    print(f"Unique market_ids in Parquet: {len(mids)}")

    # 3. Check for matches
    matches_found = 0
    for tid in gamma_tokens:
        norm_tid = _normalize_id(tid)
        if norm_tid in mids:
            print(f"MATCH FOUND for token ID {tid} -> {norm_tid}")
            matches_found += 1
            
            if matches_found == 1:
                # Time audit for the first match
                match_df = df.filter(pl.col("market_id") == norm_tid)
                max_ts = match_df["timestamp_received"].max()
                target_dt = datetime.combine(target_date, datetime.min.time()) + timedelta(hours=12)
                target_ms = int(target_dt.timestamp() * 1000)
                if target_ms >= match_df["timestamp_received"].min():
                    print(f"Timeline OK: Target time {target_dt} is supported by data.")

    if matches_found == 0:
        print(f"NO MATCHES found in {url} for any of the tokens.")

if __name__ == "__main__":
    asyncio.run(quant_audit())
