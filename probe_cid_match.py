import asyncio
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_markets
from weatheredge.pmxt_fetcher import _download_parquet
import polars as pl

async def main():
    target_date = date(2026, 3, 3)
    legacy = await fetch_resolved_markets(target_date, target_date)
    if not legacy:
        print("No Gamma markets")
        # Try a different date if 2026-03-03 is empty (though it shouldn't be)
        return
    
    # Try multiple CIDs from the list
    cids = []
    for p, r in legacy[:10]:
        if r.get('conditionId'):
            cids.append(r.get('conditionId').lower())
    
    if "markets" in legacy[0][1]:
        for m in legacy[0][1]["markets"]:
            if m.get('conditionId'):
                cids.append(m.get('conditionId').lower())

    print(f"Searching for {len(cids)} Gamma CIDs in Parquet...")
    print(f"Sample CID: {cids[0]}")
    
    url = 'https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet'
    print(f"Downloading {url}...")
    df = _download_parquet(url)
    
    # Filter for any matching CID (case insensitive)
    # PMXT IDS are prefixed with 0x and lower case usually
    match = df.filter(pl.col('market_id').str.to_lowercase().is_in(cids))
    
    print(f"TOTAL MATCHES: {len(match)}")
    if not match.is_empty():
        print("MATCH FOUND!")
        print(match.head(5).to_dicts())
    else:
        print("NO MATCH FOUND in the entire file.")
        # One last check: maybe it's the decimal 'data' market_id?
        print("Checking decimal market_id in 'data' field...")
        # (This is slow, so we'll sample)
        # Actually, let's just check the unique market_id lengths in the file
        lengths = df['market_id'].str.len_bytes().value_counts()
        print("Unique market_id lengths in Parquet:")
        print(lengths)

if __name__ == "__main__":
    asyncio.run(main())
