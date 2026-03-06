"""
Slippage Calibration Script
Analyzes local tick data to calibrate the LAMBDA_COEFF used in Kyle's Lambda 
market impact model.

Kyle's Lambda: ΔPrice ∝ (TradeSize / sqrt(RollVolume))
"""
import polars as pl
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slippage_calibration")

LOCAL_TICK_DATA_DIR = Path("weather_tick_data")

def calibrate_lambda(slug: str):
    path = LOCAL_TICK_DATA_DIR / f"{slug}.parquet"
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return None

    logger.info(f"Analyzing {slug}...")
    df = pl.read_parquet(path)
    
    # Pre-process: cast price/size to float
    df = df.with_columns([
        pl.col("price").cast(pl.Float64),
        pl.col("size").cast(pl.Float64)
    ])

    # 1. Calculate price deltas
    # Sort by timestamp
    df = df.sort("timestamp_us")
    
    # Shift price to get ΔPrice
    df = df.with_columns([
        (pl.col("price").shift(-1) - pl.col("price")).alias("delta_p"),
        # signed_size: buy is positive, sell is negative
        pl.when(pl.col("side") == "buy").then(pl.col("size")).otherwise(-pl.col("size")).alias("signed_size")
    ])

    # 2. Get rolling volume (proxy for liquidity)
    # 1-hour rolling volume (period="1h" requires timestamp column to be datetime or duration)
    # Since timestamp_us is int, we'll convert to datetime for the rolling operation
    df = df.with_columns([
        (pl.col("timestamp_us") * 1000).cast(pl.Datetime("ns")).alias("dt")
    ])
    
    # We use a rolling window on the dataframe to sum size over the last 1h
    vol_df = df.rolling("dt", period="1h").agg(pl.col("size").sum().alias("rolling_vol_1h"))
    
    # Join back or just use the columns if they align
    df = df.with_columns(vol_df["rolling_vol_1h"])

    # 3. Filter for valid data points (non-zero deltas and non-zero volume)
    valid = df.filter(
        (pl.col("delta_p").is_not_null()) & 
        (pl.col("rolling_vol_1h") > 0) &
        (pl.col("signed_size") != 0)
    ).to_pandas()

    if len(valid) < 10:
        logger.warning(f"Insufficient data for {slug} ({len(valid)} points)")
        return None

    # 4. Regression: delta_p = λ * (signed_size / sqrt(rolling_vol_1h))
    X = valid["signed_size"] / np.sqrt(valid["rolling_vol_1h"])
    y = valid["delta_p"]
    
    # We want λ such that y ≈ λ * X
    # Using OLS (no intercept, impact should be 0 for size 0)
    lambda_est = np.dot(X, y) / np.dot(X, X)
    
    logger.info(f"Estimated λ for {slug}: {lambda_est:.4f} (based on {len(valid)} trades)")
    return lambda_est


def main():
    all_lambdas = []
    
    # Analyze all parquet files in the directory (excluding _combined and failed)
    for p in LOCAL_TICK_DATA_DIR.glob("*.parquet"):
        if p.stem.startswith("_") or p.stem == "failed":
            continue
            
        lamb = calibrate_lambda(p.stem)
        if lamb is not None and not np.isnan(lamb):
            all_lambdas.append(lamb)
            
    if all_lambdas:
        mean_lambda = np.mean(all_lambdas)
        median_lambda = np.median(all_lambdas)
        
        print("\n" + "="*50)
        print("SLIPPAGE CALIBRATION RESULTS")
        print("="*50)
        print(f"Number of markets analyzed: {len(all_lambdas)}")
        print(f"Mean LAMBDA_COEFF:         {mean_lambda:.4f}")
        print(f"Median LAMBDA_COEFF:       {median_lambda:.4f}")
        print("-"*50)
        print(f"Current setting:           0.5000")
        print(f"Recommendation:            Set LAMBDA_COEFF = {median_lambda:.4f}")
        print("="*50)
    else:
        print("No valid data found for calibration.")

if __name__ == "__main__":
    main()
