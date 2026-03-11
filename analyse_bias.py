"""
Calibration analysis for backtest results.
Usage: python analyse_bias.py my_backtest4.csv
"""
import sys
import pandas as pd
import numpy as np

csv_path = sys.argv[1] if len(sys.argv) > 1 else "my_backtest4.csv"
df = pd.read_csv(csv_path)

# Only bets that were actually placed
placed = df[df["bet_size_usd"] > 0].copy()

print(f"\n{'='*60}")
print(f"BACKTEST ANALYSIS: {csv_path}")
print(f"{'='*60}")
print(f"Date range:      {df['created_at'].min()[:10]} → {df['created_at'].max()[:10]}")
print(f"Total signals:   {len(df)}")
print(f"Bets placed:     {len(placed)}")
print(f"Zero-bet (Kelly too small): {len(df) - len(placed)}")
print(f"Total wagered:   ${placed['bet_size_usd'].sum():.2f}")
print(f"Net PnL:         ${placed['pnl_usd'].sum():.2f}")
print(f"Win rate:        {placed['won'].mean():.1%}  ({placed['won'].sum():.0f}/{len(placed)})")
print(f"ROI:             {placed['pnl_usd'].sum() / placed['bet_size_usd'].sum():.1%}")

# ── Calibration by model_prob bucket ────────────────────────────────────────
print(f"\n{'─'*60}")
print("CALIBRATION CHECK (model prob vs actual win rate)")
print(f"{'─'*60}")
print(f"{'Model prob bucket':<22} {'N':>5} {'Actual win%':>12} {'Avg model%':>12} {'Diff':>8}")

buckets = [0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.60, 1.01]
for lo, hi in zip(buckets[:-1], buckets[1:]):
    mask = (placed["model_prob"] >= lo) & (placed["model_prob"] < hi)
    grp = placed[mask]
    if len(grp) < 3:
        continue
    actual = grp["won"].mean()
    predicted = grp["model_prob"].mean()
    diff = actual - predicted
    flag = " ← COLD BIAS" if diff < -0.05 else (" ← WARM BIAS" if diff > 0.05 else "")
    print(f"  {lo:.0%}–{hi:.0%}{'':>12} {len(grp):>5} {actual:>11.1%} {predicted:>11.1%} {diff:>+8.1%}{flag}")

# ── Bin 0 deep dive ─────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print("BIN 0 DEEP DIVE (coldest temperature bin)")
print(f"{'─'*60}")
b0 = placed[placed["bin_index"] == 0]
if len(b0) == 0:
    print("  No bin 0 bets placed.")
else:
    print(f"  Bets placed:    {len(b0)}")
    print(f"  Win rate:       {b0['won'].mean():.1%}  ({b0['won'].sum():.0f}/{len(b0)})")
    print(f"  Avg model prob: {b0['model_prob'].mean():.1%}")
    print(f"  Avg mkt prob:   {b0['market_prob'].mean():.1%}")
    print(f"  Net PnL:        ${b0['pnl_usd'].sum():.2f}")
    print(f"\n  When bin 0 LOST — where did the temp actually resolve?")
    lost = b0[b0["won"] == 0]
    if len(lost) > 0:
        print(f"  {'Resolved bin':>14} {'Count':>7} {'Avg model_prob':>15}")
        for rb, grp in lost.groupby("resolved_bin"):
            print(f"  {int(rb):>14} {len(grp):>7} {grp['model_prob'].mean():>14.1%}")
    print(f"\n  Bin 0 losses by station:")
    print(f"  {'Station':<10} {'Bets':>5} {'Wins':>5} {'Win%':>7} {'PnL':>10}")
    for sid, grp in b0.groupby("station_id"):
        print(f"  {sid:<10} {len(grp):>5} {grp['won'].sum():>5.0f} {grp['won'].mean():>7.1%} ${grp['pnl_usd'].sum():>9.2f}")

# ── Performance by bin ───────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print("PERFORMANCE BY BIN INDEX")
print(f"{'─'*60}")
print(f"  {'Bin':>4} {'Bets':>6} {'Win%':>7} {'Avg model%':>11} {'Avg mkt%':>10} {'Net PnL':>10}")
for b, grp in placed.groupby("bin_index"):
    print(f"  {int(b):>4} {len(grp):>6} {grp['won'].mean():>7.1%} {grp['model_prob'].mean():>10.1%} {grp['market_prob'].mean():>10.1%} ${grp['pnl_usd'].sum():>9.2f}")

# ── Performance by station ───────────────────────────────────────────────────
print(f"\n{'─'*60}")
print("PERFORMANCE BY STATION")
print(f"{'─'*60}")
print(f"  {'Station':<10} {'Bets':>5} {'Win%':>7} {'Wagered':>9} {'Net PnL':>10} {'ROI':>7}")
for sid, grp in placed.groupby("station_id"):
    roi = grp["pnl_usd"].sum() / grp["bet_size_usd"].sum() if grp["bet_size_usd"].sum() > 0 else 0
    print(f"  {sid:<10} {len(grp):>5} {grp['won'].mean():>7.1%} ${grp['bet_size_usd'].sum():>8.2f} ${grp['pnl_usd'].sum():>9.2f} {roi:>7.1%}")

print(f"\n{'='*60}\n")
