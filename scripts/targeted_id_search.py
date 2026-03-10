import asyncio
import json
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_daily_events
from weatheredge.pmxt_fetcher import _download_parquet
import polars as pl

async def main():
    target_date = date(2026, 3, 3)
    
    # 1. Get weather markets from Gamma
    print("Fetching weather markets from Gamma...")
    events = await fetch_resolved_daily_events(target_date, target_date)
    if not events:
        print("No daily events found.")
        return
    
    # Filter Gamma markets to only weather
    weather_cids = []
    for p, r in events:
        # Check both the event CID and the child market CIDs
        cid = r.get('id')
        if cid: weather_cids.append(cid.lower())
        if 'markets' in r:
            for m in r['markets']:
                mcid = m.get('conditionId')
                if mcid: weather_cids.append(mcid.lower())

    print(f"Searching for {len(weather_cids)} weather Condition IDs in PMXT...")
    
    url = 'https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet'
    print(f"Downloading {url}...")
    df = _download_parquet(url)
    
    matches_found = 0
    # Search top-level market_id using truncated CIDs
    for cid in weather_cids:
        # Theory: PMXT truncated 32-byte CIDs to 20 bytes (0x + 40 hex chars)
        truncated = cid[:42] if cid.startswith("0x") else f"0x{cid[:40]}"
        match_df = df.filter(pl.col('market_id') == truncated)
        if not match_df.is_empty():
            print(f"SUCCESS! Found truncation match for {cid} -> {truncated}")
            matches_found += 1
            if matches_found <= 3:
                # Print one sample row to see data
                row = match_df.row(0, named=True)
                print(f"  Sample row data: {str(row['data'])[:200]}...")
            
    print(f"Total truncation matches: {matches_found}")

if __name__ == "__main__":
    asyncio.run(main())
