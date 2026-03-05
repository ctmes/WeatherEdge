import asyncio
import json
import polars as pl
from datetime import date, datetime
from weatheredge.backtest_fetchers import fetch_resolved_daily_events
from weatheredge.pmxt_fetcher import _download_parquet

async def token_id_audit():
    target_date = date(2026, 3, 3)
    print(f"--- Token ID Audit for {target_date} ---")
    
    # 1. Get Token IDs from Gamma
    events = await fetch_resolved_daily_events(target_date, target_date)
    if not events: return
    
    p, r = events[0]
    print(f"Market: {p.question}")
    
    if 'markets' not in r: return
    
    # Check first child market
    m = r['markets'][0]
    tids_dec = m.get('clobTokenIds', [])
    print(f"Gamma Decimal TIDs: {tids_dec}")
    
    hex_tids = []
    for tid in tids_dec:
        try:
            h = hex(int(tid)).lower()
            hex_tids.append(h)
        except: pass
    print(f"Gamma Hex TIDs: {hex_tids}")
    
    # 2. Search PMXT
    url = "https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet"
    print(f"Searching in {url}...")
    df = _download_parquet(url)
    
    mids = set(df["market_id"].unique().to_list())
    
    for h_tid in hex_tids:
        # Check direct
        if h_tid in mids:
            print(f"MATCH! Found Token ID {h_tid} as market_id.")
        # Check without 0x
        elif h_tid[2:] in mids:
            print(f"MATCH! Found Token ID {h_tid[2:]} (no 0x) as market_id.")
        else:
            print(f"No match for {h_tid}")

if __name__ == "__main__":
    asyncio.run(token_id_audit())
