from weatheredge.cache import read_cache
from datetime import date
import hashlib

def test_read():
    subdir = "backtest_cache"
    # key for 2026-02-22
    parts = ["gamma_res_m", "2026-02-22"]
    raw = "|".join(parts)
    key = hashlib.sha256(raw.encode()).hexdigest()[:16] + ".json"
    
    print(f"Testing read for key: {key}")
    data = read_cache(subdir, key)
    if data:
        print(f"SUCCESS: Read {len(data)} items from cache.")
    else:
        print("FAILURE: Could not read from cache.")

if __name__ == "__main__":
    test_read()
