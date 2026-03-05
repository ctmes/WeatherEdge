import polars as pl
import json
from weatheredge.pmxt_fetcher import _download_parquet

def main():
    url = 'https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet'
    print(f"Downloading {url}...")
    df = _download_parquet(url)
    
    print(f"Total rows: {len(df)}")
    
    # Search for weather related keywords in the data column
    # We search the first 100,000 rows for performance
    search_limit = 100000
    for i in range(min(search_limit, len(df))):
        row = df.row(i, named=True)
        data_str = row["data"]
        
        if any(kw in data_str.lower() for kw in ["weather", "temp", "rain", "snow", "celsius", "fahrenheit"]):
            print(f"FOUND MATCH in row {i}:")
            print(f"Top market_id: {row['market_id']}")
            try:
                data = json.loads(data_str)
                print(json.dumps(data, indent=2))
            except:
                print(data_str)
            return

    print(f"No weather-related markets found in first {search_limit} rows.")

if __name__ == "__main__":
    main()
