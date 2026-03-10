import asyncio
from datetime import date
from weatheredge.pmxt_fetcher import _download_parquet, build_date_urls, fetch_pmxt_resolved_markets
import polars as pl
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    print("Getting pairs from gamma...")
    pairs = await fetch_pmxt_resolved_markets(date(2026, 3, 3), date(2026, 3, 3))
    gamma_cids = set()
    for p, r in pairs:
        if "markets" in r and r["markets"]:
            for m in r["markets"]:
                gamma_cids.add(m.get("conditionId", m.get("id", "")))
        else:
            gamma_cids.add(p.condition_id)
            
    print(f"Extracted {len(gamma_cids)} condition IDs from Gamma.")
    print("Sample IDs:", list(gamma_cids)[:3])
    
    url = build_date_urls(date(2026, 3, 3), date(2026, 3, 3))[0][1]
    print(f"\nDownloading {url} to inspect...")
    df = _download_parquet(url)
    
    pmxt_mids = set(df["market_id"].unique().to_list())
    print(f"PMXT file has {len(pmxt_mids)} unique market_ids.")
    print("Sample PMXT IDs:", list(pmxt_mids)[:3])
    
    overlap = pmxt_mids.intersection(gamma_cids)
    print(f"\nOVERLAP: {len(overlap)} matching IDs out of {len(gamma_cids)} Gamma IDs.")
    
    if len(overlap) == 0:
        print("\nLet's check if the PMXT IDs are actually token_ids...")
        gamma_tids = set()
        for p, r in pairs:
            for t in p.token_ids:
                if t: gamma_tids.add(t)
        
        token_overlap = pmxt_mids.intersection(gamma_tids)
        print(f"OVERLAP with Gamma token_ids: {len(token_overlap)} matching IDs.")

if __name__ == "__main__":
    asyncio.run(main())
