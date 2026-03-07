import requests
import pandas as pd
import time
from pathlib import Path
from io import BytesIO
from datetime import date, timedelta

API_KEY = "tlx_9b519bd1aaa0d3955110abbe7458113a"
BASE_URL = "https://api.telonex.io/v1"
OUTPUT_DIR = Path("weather_tick_data")
OUTPUT_DIR.mkdir(exist_ok=True)

def daterange(start: str, end: str):
    """Yield each date string between start and end inclusive."""
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while d <= e:
        yield str(d)
        d += timedelta(days=1)

# ── Step 1: Load markets ─────────────────────────────────────────────────────
print("Fetching markets metadata...")
markets_df = pd.read_parquet(f"{BASE_URL}/datasets/polymarket/markets")

temp_markets = markets_df[
    markets_df['question'].str.contains(r'highest temperature', case=False, na=False) &
    markets_df['trades_from'].notna() &
    (markets_df['trades_from'] != '')
].copy()

print(f"Found {len(temp_markets)} markets")

# ── Step 2: Download per market, per day ─────────────────────────────────────
downloaded_count = 0
failed = []

for i, (_, market) in enumerate(temp_markets.iterrows()):
    slug = market['slug']
    trades_from = market['trades_from']
    trades_to = market['trades_to']

    market_dir = OUTPUT_DIR / slug
    market_dir.mkdir(exist_ok=True)

    market_frames = []
    all_days_exist = True

    for day in daterange(trades_from, trades_to):
        out_file = market_dir / f"{day}.parquet"

        if out_file.exists():
            market_frames.append(pd.read_parquet(out_file))
            continue

        all_days_exist = False

        url = f"{BASE_URL}/downloads/polymarket/trades/{day}"
        params = {"slug": slug, "outcome": "Yes"}
        headers = {"Authorization": f"Bearer {API_KEY}"}

        try:
            r = requests.get(url, params=params, headers=headers, timeout=60)

            if r.status_code == 402:
                print("⚠️  Download limit hit — upgrade to Plus at telonex.io")
                exit()
            elif r.status_code == 404:
                # No trades on this day — normal, skip silently
                continue
            elif r.status_code == 403:
                print(f"  403 Forbidden for {slug} on {day} — check API key or plan")
                failed.append({"slug": slug, "day": day, "reason": "403"})
                continue
            elif r.status_code != 200:
                print(f"  HTTP {r.status_code} for {slug} on {day}")
                failed.append({"slug": slug, "day": day, "reason": f"HTTP {r.status_code}"})
                continue

            # Validate it's actually a parquet before saving
            try:
                df = pd.read_parquet(BytesIO(r.content))
            except Exception:
                print(f"  Bad response (not parquet) for {slug} on {day}: {r.content[:200]}")
                failed.append({"slug": slug, "day": day, "reason": "not_parquet"})
                continue

            df.to_parquet(out_file, index=False)
            market_frames.append(df)
            downloaded_count += 1
            time.sleep(0.2)

        except Exception as e:
            print(f"  Exception for {slug} on {day}: {e}")
            failed.append({"slug": slug, "day": day, "reason": str(e)})

    # Combine all days for this market into one file
    if market_frames:
        combined = pd.concat(market_frames, ignore_index=True)
        combined.to_parquet(OUTPUT_DIR / f"{slug}.parquet", index=False)
        print(f"[{i+1}/{len(temp_markets)}] ✓ {slug} — {len(combined)} rows, cols: {combined.columns.tolist()}")
    else:
        print(f"[{i+1}/{len(temp_markets)}] ✗ No data: {slug}")

# ── Step 3: Combine everything ───────────────────────────────────────────────
all_files = [f for f in OUTPUT_DIR.glob("*.parquet")]

if all_files:
    final = pd.concat(
        [pd.read_parquet(f).assign(slug=f.stem) for f in all_files],
        ignore_index=True
    )
    final.to_parquet(OUTPUT_DIR / "_combined_temp_trades.parquet", index=False)
    print(f"\n✅ {len(final):,} total ticks across {len(all_files)} markets")
    print(f"Columns: {final.columns.tolist()}")

if failed:
    pd.DataFrame(failed).to_csv(OUTPUT_DIR / "failed.csv", index=False)
    print(f"⚠️  {len(failed)} failures logged to failed.csv")
