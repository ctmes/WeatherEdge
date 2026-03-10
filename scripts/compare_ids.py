import asyncio
from datetime import date
from weatheredge.backtest_fetchers import fetch_resolved_markets, fetch_resolved_daily_events
from weatheredge.pmxt_fetcher import _download_parquet, build_date_urls
import polars as pl
import json

async def main():
    target_date = date(2026, 3, 3)
    legacy = await fetch_resolved_markets(target_date, target_date)
    daily = await fetch_resolved_daily_events(target_date, target_date)
    all_p = legacy + daily
    if not all_p:
        print("No Gamma markets")
        return
    
    # Get all potential IDs from Gamma
    potential_gamma_ids = set()
    for p, r in all_p:
        # 1. conditionId
        cid = r.get('conditionId')
        if cid: potential_gamma_ids.add(cid)
        
        # 2. child market IDs
        if "markets" in r:
            for m in r["markets"]:
                if m.get('conditionId'): potential_gamma_ids.add(m.get('conditionId'))
                if m.get('clobTokenIds'):
                    # The clobTokenIds are decimal strings. 
                    # Try both as-is and as hex.
                    for tid in m.get('clobTokenIds'):
                        potential_gamma_ids.add(str(tid))
                        try:
                            potential_gamma_ids.add(hex(int(tid)))
                        except ValueError:
                            pass
    
    print(f"Collected {len(potential_gamma_ids)} potential IDs from Gamma")
    
    # Get Parquet data
    url = build_date_urls(target_date, target_date)[0][1]
    df = _download_parquet(url)
    pmxt_mids = df["market_id"].unique().to_list()
    
    def normalize(sid):
        if not sid: return ""
        sid = str(sid).lower()
        if sid.startswith("0x"): sid = sid[2:]
        return sid.lstrip("0") # strip leading zeros for comparison
    
    norm_pmxt = {normalize(m) for m in pmxt_mids}
    
    matches = []
    for gid in potential_gamma_ids:
        if normalize(gid) in norm_pmxt:
            matches.append(gid)
            
    print(f"TOTAL MATCHES: {len(matches)}")
    if matches:
        print("Sample matches:", matches[:5])
    else:
        print("NO MATCHES FOUND.")
        # Print a few normalized PMXT IDs
        print("Sample normalized PMXT IDs:", sorted(list(norm_pmxt))[:5])
        print("Sample normalized Gamma IDs:", sorted([normalize(g) for g in list(potential_gamma_ids)[:10]]))

if __name__ == "__main__":
    asyncio.run(main())
