import polars as pl
import io
import httpx

url = 'https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet'
print(f"Fetching {url}...")
r = httpx.get(url, timeout=120, follow_redirects=True)
r.raise_for_status()
df = pl.read_parquet(io.BytesIO(r.content))

mid = df["market_id"][0]
print(f"Market ID: {mid}")
print(f"Market ID type: {type(mid)}")
print(f"Market ID length: {len(mid)}")

# Check if it's hex
try:
    int(mid, 16)
    print("Market ID is valid hex.")
except ValueError:
    print("Market ID is NOT valid hex.")
