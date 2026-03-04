# Council Review: Systematic and Procedural Advice
**Date:** March 2026
**Prepared by:** WeatherEdge AI Assistant on behalf of The Council
**Based on:** Current Implementation (`pipeline.py`, `slippage.py`, `backtester.py`, `probability.py`)

---

## Executive Summary
Following the recent architectural upgrades to WeatherEdge—specifically the implementation of **dynamic slippage**, the **advanced backtester modes**, and **rolling calibrations**—the LLM Council has convened to review the *actual* state of the codebase.

While the structural foundations (Kelly sizing, fractional constraints, and regime assessments) are exceptionally sound, the Council has identified critical systematic and procedural gaps that must be addressed before risking significant bankroll.

Below are the detailed procedural changes advised by each persona.

---

## 1. The Quant: Validation and Microstructure Integrity
*“An edge on paper is just a hypothesis until the slippage penalty is empirically verified.”*

**Current State Assessment:**
The Quant acknowledges the massive improvement introduced by `slippage.py` (Tier 1: Walk-the-book, Tier 2: Kyle's Lambda, Tier 3: Volume heuristic). The edge calculation in `probability.py` now correctly penalises theoretical edges based on market depth. Furthermore, `backtester.py` generates Brier Skill Scores against uniform naive baselines.

**Advised Procedural Changes:**
1. **Empirical Validation of Kyle’s Lambda:** The coefficient `0.5` in `volume_impact_estimate` is currently a heuristic. 
   - *Action required:* Procedure must be implemented to measure *actual* slippage during paper trading versus *estimated* slippage. If the model systematically underestimates slippage, the edge is a mirage. Log `expected_fill_price` vs `actual_fill_price` for every trade, and continuously retrain the `LAMBDA_COEFF`.
2. **True Out-of-Sample Calibration Testing:** Your new `calibrate_all.py` script likely applies a rolling window. The Quant demands walk-forward testing.
   - *Action required:* Do not just track Brier Score on the training data. The backtester must be modified to use the trailing 60-day calibration to bet on day 61. If the Brier Skill Score drops significantly on Day 61 compared to the trailing window, the calibration models are overfitting.
3. **Station-Level BSS Tracking:**
   - *Action required:* Brier Skill Scores in the backtest are aggregated globally. You must track BSS *per station*. A BSS of 0.15 globally might hide the fact that Melbourne (YMML) has a BSS of -0.05, destroying P&L while Wellington masks the failure.

---

## 2. The Meteorologist: Domain and Regime Blind Spots
*“The pipeline sees numbers; the atmosphere produces physics. Numbers without physics are dangerous.”*

**Current State Assessment:**
`pipeline.py` correctly requests `AtmosphericRegime` flags and applies a `confidence_multiplier` to the edge. This is a massive leap forward from treating all forecasts equally.

**Advised Procedural Changes:**
1. **Mandatory Post-Mortem on 3°C+ Misses:**
   - *Action required:* Create a systematic procedure: Whenever a forecast misses the resolved outcome by more than 3°C, the system must trigger an alert for manual meteorological review. Was it a timing error on a cold front? Was it unaccounted-for CAPE? The `RegimeAssessment` logic in `regime.py` should only be updated *after* these post-mortems confirm a missed failure mode.
2. **Dynamic Lead-Time Degradation:** 
   - *Action required:* Currently, the regime assessment applies static penalties for lead times (>72h, >120h). Procedurally, this needs to be station-dependent. A 72-hour forecast for a stable winter anticyclone in Tokyo is more reliable than a 24-hour forecast during a Melbourne spring transition. Tie lead-time multipliers directly to seasonal variance distributions.

---

## 3. The Adversary: Competitive Latency and Order Book Realities
*“You are calculating edge against the ghost of the market. The institutional bots have already taken the meat.”*

**Current State Assessment:**
The backtester in `backtester.py` now ingests actual Polymarket resolved outcomes (via PMXT or Gamma) and calculates P&L based on simulated bets.

**Advised Procedural Changes:**
1. **Replay Latency, Not Just Prices:** 
   - *Action required:* The current backtester likely evaluates the forecast model against a snapshot or end-of-day market price. This is competitively fatal. Institutional bots scrape NOAA/ECMWF runs the millisecond they drop and consume the liquidity. Procedurally, you must timestamp the exact minute the weather model data becomes publicly available, and pull the Polymarket order book *for that exact minute*. If your edge only existed for 90 seconds, your 30-minute scheduler will never capture it in live trading.
2. **Spoofing Filters in Thin Markets:**
   - *Action required:* For markets with `<$5,000` volume, the midpoint is easily manipulated by spoofing bots trying to paint the tape. Update `pipeline.py` to discard prices where the bid/ask spread is arbitrarily wide or asymmetric relative to total volume, to prevent WeatherEdge from betting into a spoof trap.

---

## 4. The Risk Manager: Hard Ceilings and Systemic Failure
*“Soft constraints are suggestions. Only hard circuit breakers guarantee survival.”*

**Current State Assessment:**
`check_position_limits()` in `pipeline.py` excellently enforces the `<3%` single bet maximum and checks current market exposure.

**Advised Procedural Changes:**
1. **Implement Hard State-Level Circuit Breakers:**
   - *Action required:* Currently, if a limit is hit, the pipeline logs a debug message (`"Position limit blocked: %s"`) and skips the trade. This is a soft constraint. If the system is undergoing a massive drawdown (e.g., losing 15% in a month), skipping trades is not enough. You must implement a database-level `SYSTEM_HALTED` flag. If total bankroll falls by `15%`, the orchestrator must *refuse to run* until a human manually overrides the state flag via a command-line resolution.
2. **Correlated Synoptic Exposure Tracking:**
   - *Action required:* Betting on Sydney, Melbourne, and Brisbane simultaneously during an unpredicted Australian East Coast low will bypass the single-market 8% limit, but expose 24% of the bankroll to the *exact same underlying weather error*. Procedurally, the system must tag markets by weather system/region and strictly cap total regional exposure to 15%.

---

## Synthesis & Next Steps
Instead of just fine-tuning the probability math, The Council overwhelmingly advises focusing the next development cycles on **infrastructure robustness**. 
* **The priority:** Re-engineer the backtester to replay tick-level data synchronized chronologically to NOAA/ECMWF release timestamps (The Adversary's mandate), and implement hard, manual-override circuit breakers (The Risk Manager's mandate). Only then should scaling capital be considered.
