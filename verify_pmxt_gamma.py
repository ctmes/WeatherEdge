import asyncio
import polars as pl
import logging
import sys
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_daily_events
from weatheredge.pmxt_fetcher import _download_parquet

logging.basicConfig(level=logging.INFO)

async def verify():
    target_date = date(2026, 3, 3)
    with open("verify_res.txt", "w") as f:
        f.write(f"--- Verification for {target_date} ---\n")
        
        # 1. Fetch from Gamma
        f.write("Fetching Gamma events...\n")
        events = await fetch_resolved_daily_events(target_date, target_date)
        if not events:
            f.write("No Gamma events found.\n")
            return
            
        p, r = events[0]
        f.write(f"Sample Question: {p.question}\n")
        
        token_mapping = []
        if 'markets' in r:
            for m in r['markets']:
                desc = m.get('groupItemTitle', '')
                cid = m.get('conditionId')
                clob_ids = m.get('clobTokenIds')
                
                if isinstance(clob_ids, str) and clob_ids.startswith('['):
                    import json
                    try:
                        clob_ids = json.loads(clob_ids)
                    except:
                        pass
                elif isinstance(clob_ids, str):
                    clob_ids = [clob_ids]
                    
                token_mapping.append({
                    'desc': desc,
                    'cid': cid,
                    'clob_ids': clob_ids
                })
        
        f.write("\nGamma Token IDs (Decimal):\n")
        for item in token_mapping[:5]:
            f.write(f"  {item['desc']}: CID={item['cid']}\n")
            f.write(f"    clobTokenIds={item['clob_ids']}\n")
            if item['clob_ids']:
                for tid in item['clob_ids']:
                    try:
                        h = hex(int(tid))
                        f.write(f"    -> hex(int()): {h}\n")
                    except:
                        f.write(f"    -> hex conversion failed for {tid}\n")

        # 2. Inspect Parquet
        url = f"https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet"
        f.write(f"\nInspecting PMXT Parquet {url}...\n")
        df = _download_parquet(url)
        
        mids = df["market_id"].unique().to_list()
        f.write(f"Sample PMXT market_ids (mids):\n")
        for mid in mids[:10]:
            f.write(f"  {mid}\n")

        # 3. Try to find a match
        f.write("\nSearching for matches...\n")
        for item in token_mapping:
            if not item['clob_ids']: continue
            for tid in item['clob_ids']:
                try:
                    ival = int(tid)
                    full_hex = hex(ival).lower()
                    
                    # Normalize: ensure 0x prefix and lowercase
                    if not full_hex.startswith("0x"):
                        full_hex = "0x" + full_hex
                    
                    # Standard 256-bit is 64 hex chars. 
                    # Python's hex() doesn't pad.
                    content = full_hex[2:]
                    norm_64 = "0x" + content.zfill(64)
                    
                    # Check truncated version (40 chars content = 42 total)
                    trunc_40 = "0x" + content.zfill(64)[:40]
                    
                    if norm_64 in mids:
                        f.write(f"  [SUCCESS] MATCH FOUND (Full 64): {item['desc']} ({tid}) -> {norm_64}\n")
                    elif trunc_40 in mids:
                        f.write(f"  [SUCCESS] MATCH FOUND (Truncated 40): {item['desc']} ({tid}) -> {trunc_40}\n")
                    elif full_hex in mids:
                        f.write(f"  [SUCCESS] MATCH FOUND (Raw hex): {item['desc']} ({tid}) -> {full_hex}\n")
                    else:
                        # Bruteforce check
                        found = False
                        for mid in mids:
                            if mid.lower() == full_hex or mid.lower() == norm_64 or mid.lower() == trunc_40:
                                f.write(f"  [SUCCESS] BRUTE MATCH: {item['desc']} ({tid}) -> {mid}\n")
                                found = True
                                break
                        if not found:
                             pass # No match for this token
                except Exception as e:
                    f.write(f"  Error processing tid {tid}: {e}\n")

if __name__ == "__main__":
    asyncio.run(verify())
