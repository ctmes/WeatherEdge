import polars as pl
import io
import httpx
import json

url = 'https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet'
r = httpx.get(url, timeout=120, follow_redirects=True)
r.raise_for_status()
df = pl.read_parquet(io.BytesIO(r.content))

# Look for a specific market_id
target_id = "0x5e2bf93a127cb49b2ebd51b752945d7a6e709e99a8490d61d1f0e47c8845"
row = df.filter(pl.col("market_id") == target_id).head(1)
if not row.is_empty():
    data = json.loads(row["data"][0])
    print(json.dumps(data, indent=2))
else:
    print("ID not found")
