import asyncio
import json
import polars as pl
from weatheredge.pmxt_fetcher import _download_parquet

def keyword_search():
    url = "https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet"
    print(f"Searching for weather keywords in {url}...")
    df = _download_parquet(url)
    
    # Common weather cities or terms
    keywords = ["NYC", "New York", "Chicago", "London", "Temp", "Temperature"]
    
    found_count = 0
    for i, d_str in enumerate(df["data"].to_list()):
        for kw in keywords:
            if kw.lower() in d_str.lower():
                row = df.row(i, named=True)
                print(f"\n--- MATCH FOUND (Keyword: {kw}) ---")
                print(f"market_id: {row['market_id']}")
                print(f"data: {d_str[:500]}...")
                found_count += 1
                break
        if found_count >= 5: break
    
    if found_count == 0:
        print("No matches found for keywords in the first 1M rows.")

if __name__ == "__main__":
    keyword_search()
