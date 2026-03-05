import asyncio
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_markets, fetch_resolved_daily_events
from weatheredge.pmxt_fetcher import _download_parquet, build_date_urls
import polars as pl
import json
import sys

async def main():
    target_date = date(2026, 3, 3)
    # Step 1: Gamma IDs
    legacy = await fetch_resolved_markets(target_date, target_date)
    daily = await fetch_resolved_daily_events(target_date, target_date)
    all_p = legacy + daily
    
    gamma_ids = set()
    for p, r in all_p:
        if p.condition_id: gamma_ids.add(p.condition_id.lower())
        if r.get('conditionId'): gamma_ids.add(r.get('conditionId').lower())
        if "markets" in r:
            for m in r["markets"]:
                if m.get('conditionId'): gamma_ids.add(m.get('conditionId').lower())
                if m.get('clobTokenIds'):
                    for tid in m.get('clobTokenIds'):
                        gamma_ids.add(str(tid))

    # Step 2: PMXT IDs
    url = build_date_urls(target_date, target_date)[0][1]
    df = _download_parquet(url)
    pmxt_ids = set(df["market_id"].unique().to_list())
    
    # Analysis
    res = []
    res.append(f"Gamma IDs: {len(gamma_ids)}")
    res.append(f"PMXT IDs: {len(pmxt_ids)}")
    
    overlap = gamma_ids.intersection(pmxt_ids)
    res.append(f"Direct overlap: {len(overlap)}")
    
    # Try hex-to-dec
    gamma_hex_as_dec = set()
    for gid in gamma_ids:
        if gid.startswith("0x"):
            try:
                gamma_hex_as_dec.add(str(int(gid, 16)))
            except ValueError:
                pass
    
    overlap_dec = gamma_hex_as_dec.intersection(pmxt_ids)
    res.append(f"Hex-to-decimal overlap: {len(overlap_dec)}")
    
    if overlap_dec:
        res.append("Sample matching (Hex -> Dec):")
        for gid in list(gamma_ids):
            if gid.startswith("0x"):
                dec = str(int(gid, 16))
                if dec in pmxt_ids:
                    res.append(f"  {gid} -> {dec}")
                    break

    with open('inspect_results.txt', 'w', encoding='utf-8') as f:
        f.write("\n".join(res))
    print("Results written to inspect_results.txt")

if __name__ == "__main__":
    asyncio.run(main())
