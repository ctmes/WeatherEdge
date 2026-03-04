import polars as pl
import io
import os

print("Testing polars parquet read...")
df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
buf = io.BytesIO()
df.write_parquet(buf)
buf.seek(0)

# Read it back
df2 = pl.read_parquet(buf)
print("Read successful.")
print("Exiting...")
