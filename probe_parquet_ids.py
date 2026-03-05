import polars as pl
import json
from weatheredge.pmxt_fetcher import _download_parquet

def main():
    url = 'https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet'
    df = _download_parquet(url)
    
    print(f"Total rows: {len(df)}")
    
    # Sample a few rows
    for i in range(min(10, len(df))):
        row = df.row(i, named=True)
        top_mid = row["market_id"]
        update_type = row["update_type"]
        
        try:
            data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
            data_mid = data.get("market_id")
            token_id = data.get("token_id") # Some might have token_id instead
            
            print(f"Row {i} [{update_type}]:")
            print(f"  Top market_id:  {top_mid}")
            print(f"  Data market_id: {data_mid}")
            print(f"  Data token_id:  {token_id}")
        except Exception as e:
            print(f"Row {i} Error: {e}")

if __name__ == "__main__":
    main()
