# WeatherEdge

> Systematic weather prediction market trading — find the gap between meteorological models and Polymarket prices.

WeatherEdge is a quantitative trading system that identifies and exploits pricing inefficiencies between high-resolution ensemble weather forecasts and Polymarket's weather prediction markets. It combines probabilistic meteorology (ECMWF/GFS ensemble models), station-level historical calibration, atmospheric regime analysis, and Kelly-optimal position sizing into a fully automated paper-trading and backtesting pipeline.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Core Capabilities](#core-capabilities)
- [Prerequisites & Installation](#prerequisites--installation)
- [Configuration](#configuration)
- [CLI Reference](#cli-reference)
- [Data Pipeline](#data-pipeline)
- [Station Calibration](#station-calibration)
- [Risk Management](#risk-management)
- [Backtesting](#backtesting)
- [Dashboard](#dashboard)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Dependencies](#dependencies)
- [Disclaimer](#disclaimer)

---

## Architecture Overview

```
Polymarket CLOB          Open-Meteo API         Weather Underground
(active markets)    +   (ensemble forecasts)  +  (historical observations)
        │                       │                          │
        ▼                       ▼                          ▼
  polymarket.py           forecast.py              mesonet.py / calibration.py
  (market parser)      (ECMWF/GFS blending)       (seasonal bias correction)
        │                       │                          │
        └───────────────────────┴──────────────────────────┘
                                │
                          pipeline.py
                    (main scan orchestrator)
                                │
               ┌────────────────┼──────────────────┐
               ▼                ▼                   ▼
          regime.py        probability.py       safety.py
       (circuit breakers)  (Kelly sizing,     (position limits,
                            edge calculation)   rate limits)
               │                │                   │
               └────────────────┴───────────────────┘
                                │
                           Signal → db.py (SQLite / PostgreSQL)
                                │
                    ┌───────────┴────────────┐
                    ▼                        ▼
              resolver.py             analytics.py
          (resolution scoring:      (Sharpe, drawdown,
           Brier, RPS, LogScore)       win rate)
```

**Async-first design:** all I/O (HTTP, database, WebSocket) runs on `asyncio` with `httpx` and SQLAlchemy 2.0 async sessions.

---

## Core Capabilities

### Forecast Engine
- **51-member ECMWF ensemble** via Open-Meteo, providing full probability distributions over temperature and precipitation
- **Multi-model fallback**: GFS → BOM when primary model is unavailable
- **Ensemble spread metrics**: standard deviation across members used as uncertainty proxy
- **CAPE and wind data** fetched alongside temperature for regime assessment

### Station Calibration
- Per-station, per-season bias correction derived from historical forecast vs. observation comparisons
- Significance testing (p-values) to prevent overfitting on thin data
- RMSE and lead-time degradation tracking
- Supported stations: KJFK, KLGA, KNYC, KORD, KBOS, KSFO, KLAX, KDEN, KDFW, KIAH, KMIA, KATL, KPHX, KSEA, NZWN, EGLL, and more

### Probability & Sizing
- Forecast ensemble → bin probability distribution using normal CDF or empirical quantiles
- **Edge calculation**: `edge = model_probability − market_implied_probability`
- **Kelly criterion**: quarter-Kelly with bankroll-relative caps (10% per market, 60% total exposure)
- **Dynamic slippage**: tier-based model (orderbook depth → spread+impact → volume heuristic) using Kyle's Lambda

### Atmospheric Regime Assessment
- Model divergence circuit breaker: no trade if GFS/ECMWF diverge > 3°C
- Ensemble spread circuit breaker: reduced sizing at 4°C, halt at 6°C
- CAPE flags for convective instability
- Station-specific wind risk flags

### Market Discovery
- Keyword and series-slug matching against all active Polymarket markets
- Description parsing to extract station ID, metric (TMAX/TMIN/precip), resolution date, and bin boundaries
- Series blocklist to exclude non-weather markets (elections, crypto, sports)

### Execution & Persistence
- Paper trading with full signal lifecycle: generated → logged → resolved → scored
- Duplicate prevention via execution guard across restarts
- WebSocket-based live orderbook streaming for real-time price midpoints
- Automatic database migration on startup

---

## Prerequisites & Installation

**Requirements:** Python 3.11+

```bash
# Clone
git clone https://github.com/ctmes/WeatherEdge.git
cd WeatherEdge

# Install (production)
pip install .

# Install (development — includes pytest, pytest-asyncio)
pip install -e ".[dev]"

# Install (with Streamlit dashboard)
pip install -e ".[dashboard]"
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | No | SQLAlchemy URL. Defaults to `sqlite+aiosqlite:///data/weatheredge.db` |
| `OPEN_METEO_API_KEY` | No | Enables paid Open-Meteo tier (higher rate limits) |
| `WU_API_KEY` | No | Weather Underground API key for historical observations |
| `NOAA_CDO_TOKEN` | No | NOAA Climate Data Online token |
| `VERIFY_SSL` | No | Set `false` to disable SSL verification (not recommended) |

**Trading parameters** are set in `src/weatheredge/config.py` via the `TradingConfig` dataclass:

| Parameter | Default | Description |
|---|---|---|
| `min_edge_threshold` | `0.03` | Minimum edge (3%) required to generate a signal |
| `kelly_fraction` | `0.25` | Quarter-Kelly multiplier |
| `kelly_max_bet` | `0.10` | Maximum bet size as fraction of bankroll |
| `max_single_market_exposure` | `0.10` | Maximum exposure per market (10%) |
| `max_total_exposure` | `0.60` | Maximum total deployed capital (60%) |
| `polymarket_fee` | `0.02` | Platform fee assumed in edge calculation |
| `model_divergence_no_bet` | `3.0°C` | GFS/ECMWF divergence threshold — halt trading |
| `ensemble_spread_reduce` | `4.0°C` | Ensemble spread threshold — reduce sizing 50% |
| `ensemble_spread_no_bet` | `6.0°C` | Ensemble spread threshold — halt trading |

---

## CLI Reference

All commands are accessed via the `weatheredge` entry point.

### `scan` — Single pipeline scan

Run one full scan across all active markets and print signals to stdout.

```bash
weatheredge scan --bankroll 10000
weatheredge scan --bankroll 10000 -v          # Verbose: show all markets, not just signals
weatheredge scan --bankroll 10000 --deep      # Force-fetch all markets (skip cache)
```

### `diagnose` — Detailed diagnostics

Full scan with per-market reasoning — shows exactly why each market was included or excluded.

```bash
weatheredge diagnose --bankroll 5000
weatheredge diagnose --bankroll 5000 --json   # Machine-readable JSON output
```

### `run` — Paper trading loop

Start the automated paper-trading scheduler. Scans every `--interval` minutes and logs signals to the database.

```bash
weatheredge run --bankroll 10000              # 30-minute interval (default)
weatheredge run --bankroll 10000 --interval 15
weatheredge run --once                        # Single cycle, then exit
```

### `live-paper` — Real-time WebSocket trading

Subscribe to live Polymarket orderbook updates and evaluate signals on streaming prices.

```bash
weatheredge live-paper --bankroll 10000
weatheredge live-paper --bankroll 10000 --interval 10   # Re-evaluate every 10 seconds
```

### `calibrate` — Station calibration

Build or update bias correction for a specific ICAO station from historical data.

```bash
weatheredge calibrate NZWN                   # Default 90-day history
weatheredge calibrate KJFK --days 180
```

### `calibrate-all` — Bulk calibration

Recalibrate all supported stations.

```bash
weatheredge calibrate-all                    # Fetches fresh data from API
weatheredge calibrate-all --force            # Re-run even if recent calibration exists
weatheredge calibrate-all --from-cache       # Load from local cache — no API calls
```

### `resolve` — Resolution tracking

Check which paper trades have resolved on Polymarket, record outcomes, and compute forecast skill scores.

```bash
weatheredge resolve
```

### `inspect` — Forecast vs. observation analysis

Compare forecasted probabilities to actual observations for a station and date range. Useful for diagnosing calibration quality.

```bash
weatheredge inspect KLGA 2024-02-15
weatheredge inspect KLGA 2024-02-01 --end 2024-02-28 --metric tmin
weatheredge inspect KLGA 2024-02-15 --over 80    # Only show cases where market was >80% confident
weatheredge inspect KLGA 2024-02-15 --under 30   # Only show cases where market was <30%
```

### `backtest` — Historical replay

Replay a historical period using archived Polymarket prices and Open-Meteo hindcasts.

```bash
weatheredge backtest \
  --start 2024-01-01 \
  --end   2024-03-01 \
  --bankroll 10000 \
  --mode full_sim

# Options:
#   --model ecmwf_ifs | gfs | ensemble    Forecast model to use
#   --source clob | pmxt | gamma          Historical price data source
#   --walk-forward                        Walk-forward validation (avoids lookahead)
#   --latency-seconds 60                  Simulate execution latency
```

### `probe-pmxt` — Archive connectivity test

Test connectivity to the pmxt.dev Parquet archive.

```bash
weatheredge probe-pmxt --date 2026-03-03
```

### `dashboard` — Streamlit visualization

Launch the real-time monitoring dashboard.

```bash
weatheredge dashboard
weatheredge dashboard --port 8502
```

---

## Data Pipeline

Each scan cycle executes the following steps in order:

1. **Fetch markets** — Pull active weather markets from Polymarket CLOB and Gamma APIs
2. **Enrich prices** — Fetch live orderbook snapshots; compute midpoint prices per bin
3. **Group by station** — Deduplicate and cluster markets by ICAO station ID
4. **Fetch forecasts** — Query Open-Meteo ensemble for each unique station (parallel async requests)
5. **Fetch regime data** — Retrieve CAPE, wind, model divergence for each station
6. **Apply calibration** — Load station bias corrections from database; adjust forecast means
7. **Evaluate regime** — Check circuit breaker conditions; set confidence multipliers
8. **Calculate edge** — Compute `model_prob − market_prob` per bin per market
9. **Apply sizing** — Kelly fraction × bankroll × confidence multiplier, capped by exposure limits
10. **Filter signals** — Retain only markets where `edge ≥ min_edge_threshold` after fees
11. **Log to database** — Persist signals with full metadata for resolution and scoring

---

## Station Calibration

Calibration corrects for systematic biases in the forecast models at specific observation stations. Without calibration, ECMWF may consistently over- or under-forecast maximum temperatures at a given airport by 1–2°C depending on season — a material error when Polymarket bins are typically 2°C wide.

**How it works:**

1. Historical forecasts (lead time T+24 to T+120) are compared against Weather Underground observations for each station
2. Bias = mean(forecast − observation), computed per season (DJF, MAM, JJA, SON)
3. Corrections with insufficient data or high p-values are discarded
4. RMSE and lead-time degradation curves are stored to weight confidence by forecast horizon

**Viewing calibration status:**

```bash
weatheredge calibrate-all --from-cache       # Populate DB from cache without API calls
weatheredge diagnose --bankroll 1 -v         # Shows active calibration per station in scan output
```

---

## Risk Management

WeatherEdge enforces multiple independent risk layers:

| Layer | Mechanism | Default |
|---|---|---|
| **Minimum edge** | No signal below threshold | 3% net of fees |
| **Kelly cap** | Max bet per market | 10% of bankroll |
| **Total exposure** | Max simultaneous deployment | 60% of bankroll |
| **Model divergence** | GFS vs ECMWF disagreement | Halt at 3°C gap |
| **Ensemble spread** | Width of forecast distribution | Reduce at 4°C, halt at 6°C |
| **Data freshness** | Reject stale market prices | >15 min triggers `StaleDataError` |
| **Execution rate limit** | Prevent runaway execution | `ExecutionRateLimitError` guard |
| **Duplicate prevention** | Cross-restart execution guard | Deduplication via DB signal index |

---

## Backtesting

The backtesting engine replays a historical window using archived market price data and hindcast forecasts to evaluate strategy performance and forecast skill.

**Metrics computed:**

| Metric | Description |
|---|---|
| **Brier Score** | Mean squared error of probability forecasts; lower = better |
| **RPS (Ranked Probability Score)** | Brier score extended to multi-bin markets |
| **Logarithmic Score** | Log-likelihood of forecast; penalizes confident wrong calls heavily |
| **Sharpe Ratio** | Risk-adjusted return (annualized) |
| **Max Drawdown** | Largest peak-to-trough loss |
| **Win Rate** | Fraction of signals that resolved correctly |
| **Total PnL** | Net profit/loss over the backtest period |

**Data sources for historical prices:**

- `clob` — Polymarket CLOB price history API
- `pmxt` — pmxt.dev Parquet archive (high-fidelity tick data)
- `gamma` — Polymarket Gamma API

**Walk-forward validation** is supported via `--walk-forward` to prevent look-ahead bias in calibration.

---

## Dashboard

The Streamlit dashboard provides real-time visibility into the trading system.

```bash
weatheredge dashboard --port 8501
```

**Panels:**
- Active signals with edge, Kelly size, and regime flags
- Cumulative P&L and Brier score over time
- Per-station calibration status
- Recent scan logs and error events

---

## Testing

```bash
# Run all tests
pytest

# Skip tests that require live API calls
pytest -m "not integration"

# Run a specific test
pytest -k "test_brier"

# Verbose output
pytest -v
```

**Test structure:**

| File | Coverage |
|---|---|
| `test_analytics.py` | Performance metric calculations |
| `test_backtester.py` | Full backtest replay and scoring |
| `test_cache.py` | Disk cache read/write/expiry |
| `test_matching.py` | Market parser and bin boundary extraction |
| `test_integration.py` | End-to-end pipeline against live APIs |
| `test_paper_trading.py` | Signal logging, resolution, and scoring |
| `test_pmxt_fetcher.py` | pmxt archive parsing |
| `test_phase2.py` | End-to-end workflow validation |

All tests use in-memory SQLite with auto-bootstrapped schema to avoid side effects.

---

## Project Structure

```
WeatherEdge/
├── src/weatheredge/
│   ├── pipeline.py           # Main scan orchestrator
│   ├── cli.py                # CLI entry point (argparse)
│   ├── polymarket.py         # Polymarket CLOB + Gamma market scraper
│   ├── forecast.py           # Open-Meteo ensemble + multi-model fallback
│   ├── calibration.py        # Station bias correction engine
│   ├── probability.py        # Probability math, Kelly sizing, scoring metrics
│   ├── regime.py             # Atmospheric regime flags and circuit breakers
│   ├── safety.py             # Risk guards: exposure limits, rate limits
│   ├── slippage.py           # Dynamic slippage estimation
│   ├── models.py             # SQLAlchemy ORM models + in-memory dataclasses
│   ├── db.py                 # Async database layer, DDL, migrations
│   ├── config.py             # Environment loading, TradingConfig
│   ├── scheduler.py          # 30-minute paper trading loop
│   ├── live_paper.py         # WebSocket real-time execution
│   ├── ws_client.py          # Polymarket WebSocket client
│   ├── execution.py          # Signal execution guards
│   ├── resolver.py           # Resolution tracking and scoring
│   ├── backtester.py         # Historical replay engine
│   ├── backtest_fetchers.py  # CLOB, pmxt, Gamma data sources
│   ├── pmxt_fetcher.py       # pmxt.dev Parquet integration
│   ├── analytics.py          # Sharpe, drawdown, win rate aggregation
│   ├── stations.py           # ICAO station registry (coords, timezone)
│   ├── mesonet.py            # Weather Underground historical observations
│   ├── dashboard.py          # Streamlit dashboard
│   ├── cache.py              # Disk-based API response cache
│   └── logger.py             # Structured scan/signal/resolution logging
├── tests/                    # 17 test files
├── docs/
│   ├── godpaper.md           # Project specification and design rationale
│   └── archive/              # Troubleshooting notes
├── personas/                 # Design review documents (meteorologist, quant, risk)
├── scripts/                  # Utility and diagnostic scripts
├── data/                     # Local database and cache files
├── logs/                     # Execution logs
├── .env.example              # Configuration template
└── pyproject.toml            # Dependencies and package metadata
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `httpx` | Async HTTP client for all API requests |
| `sqlalchemy` | ORM and async database layer |
| `aiosqlite` | Async SQLite driver |
| `scipy` | Normal CDF, statistical tests |
| `numpy` | Numerical math, ensemble quantiles |
| `pandas` | Backtest report dataframes |
| `polars` | High-performance Parquet parsing (pmxt) |
| `python-dotenv` | `.env` file loading |
| `websockets` | Polymarket live orderbook stream |
| `streamlit` | Dashboard (optional, `[dashboard]` extra) |
| `pytest` / `pytest-asyncio` | Test suite (optional, `[dev]` extra) |

---

## Disclaimer

WeatherEdge is an experimental research tool for studying prediction market efficiency. It executes paper trades only — no real capital is deployed. Past backtest performance does not guarantee future results. Use at your own risk.
