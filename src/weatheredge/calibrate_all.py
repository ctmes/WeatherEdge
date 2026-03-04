"""
Bulk station calibration runner.

Iterates through all supported stations, runs the calibration engine
for the past 365 days, and saves the resulting bias corrections
to the database.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from weatheredge.calibration import run_calibration
from weatheredge.db import get_session, init_db, upsert_calibration
from weatheredge.stations import STATIONS

logger = logging.getLogger("weatheredge.calibrate_all")

async def run_all_calibrations(days: int = 365) -> dict[str, dict]:
    """
    Run calibration for all registered stations and persist to DB.
    
    Returns a dictionary of metrics per station.
    """
    await init_db()
    
    results = {}
    total = len(STATIONS)
    
    logger.info("Starting bulk calibration for %d stations (lookback: %d days)", total, days)
    
    # We want to run them sequentially or with limited concurrency 
    # to avoid slamming the IEM and Open-Meteo APIs.
    # The run_calibration function already uses a semaphore internally 
    # for its own parallel fetches, so sequential at the station level is safest.
    
    for i, (station_id, station) in enumerate(STATIONS.items(), 1):
        if station_id == "GLOBAL":
            # Skip the dummy global station
            continue
            
        logger.info("[%d/%d] Calibrating %s (%s, %s)...", i, total, station_id, station.city, station.country)
        
        try:
            bias, metrics = await run_calibration(station_id, days=days)
            
            if "error" in metrics:
                logger.error("  ✗ Failed for %s: %s", station_id, metrics["error"])
                results[station_id] = {"status": "error", "error": metrics["error"]}
                continue
                
            # Persist to database
            async with get_session() as session:
                await upsert_calibration(session, bias)
                
            improvement = metrics.get('oos_improvement_pct', 0.0)
            status = "improved" if improvement > 0 else "degraded"
            
            logger.info(
                "  ✓ Saved %s. RMSE: %.2fC, OOS Improvement: %+.1f%% (%s)", 
                station_id, bias.tmax_rmse, improvement, status
            )
            
            results[station_id] = {
                "status": "success",
                "improvement_pct": improvement,
                "tmax_rmse": bias.tmax_rmse,
                "train_size": metrics.get("train_size"),
            }
            
        except Exception as e:
            logger.exception("  ✗ Unexpected error calibrating %s: %s", station_id, e)
            results[station_id] = {"status": "error", "error": str(e)}

    # Print summary
    successful = sum(1 for r in results.values() if r["status"] == "success")
    improved = sum(1 for r in results.values() if r.get("improvement_pct", 0) > 0)
    
    logger.info("==================================================")
    logger.info("Bulk Calibration Complete")
    logger.info("  Total Attempted:   %d", len(results))
    logger.info("  Successful:        %d", successful)
    logger.info("  Improved OOS:      %d", improved)
    logger.info("==================================================")
    
    return results

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    asyncio.run(run_all_calibrations())
