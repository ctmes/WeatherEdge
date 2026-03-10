import polars as pl
import json
from weatheredge.pmxt_fetcher import _download_parquet
from datetime import date

def inspect_pmxt():
    url = "https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet"
    print(f"Downloading {url}...")
    df = _download_parquet(url)
    
    snapshots = df.filter(pl.col("update_type") == "book_snapshot")
    print(f"Total snapshots: {len(snapshots)}")
    
    # Sample a few rows
    sample = snapshots.head(20)
    for row in sample.iter_rows(named=True):
        try:
            data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
            print(f"Market ID: {row['market_id']}")
            print(f"  best_bid: {data.get('best_bid')} ({type(data.get('best_bid'))})")
            print(f"  best_ask: {data.get('best_ask')} ({type(data.get('best_ask'))})")
        except Exception as e:
            print(f"Error parsing row: {e}")

if __name__ == "__main__":
    inspect_pmxt()
