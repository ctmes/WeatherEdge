# WeatherEdge

**Polymarket weather trading bot — find the gap between forecast models and market prices.**

WeatherEdge is a systematic trading bot designed to find and exploit edges in Polymarket's weather markets. It bridges the gap between high-resolution meteorological forecasts (Open-Meteo) and prediction market prices, incorporating station-specific calibration and atmospheric regime assessment.

## 🚀 Key Features

- **Market Scanning**: Automatically fetches and parses active weather markets from Polymarket.
- **Ensemble Forecasts**: Utilizes multi-model ensemble data (ECMWF, GFS, etc.) via the Open-Meteo API.
- **Station Calibration**: Applies historical bias correction for specific ICAO weather stations (e.g., KORD, NZWN).
- **Atmospheric Regime Assessment**: Evaluates atmospheric stability and model confidence to adjust risk and edge thresholds.
- **Kelly Sizing**: Calculates optimal bet sizes using Kelly criterion with built-in safety limits and slippage assumptions.
- **Paper Trading Scheduler**: Runs periodic scans and logs potential trades to a local database for performance tracking.
- **Backtesting Suite**: Replays historical market data to evaluate strategy performance and forecast skill (Brier score).
- **Dashboard**: Real-time Streamlit dashboard for monitoring active signals, performance metrics, and historical logs.

## 🛠️ Project Structure

```text
weather/
├── src/weatheredge/
│   ├── pipeline.py       # Main orchestrator (fetch -> calibrate -> edge -> signal)
│   ├── polymarket.py     # Polymarket API and market parsing
│   ├── forecast.py       # Open-Meteo API and forecast processing
│   ├── calibration.py    # Station-specific bias correction
│   ├── regime.py         # Atmospheric regime and confidence assessment
│   ├── probability.py    # Probability distributions and Kelly sizing
│   ├── safety.py         # Position limits and risk guards
│   ├── cli.py            # Command-line interface
│   └── dashboard.py      # Streamlit dashboard implementation
├── tests/                # Comprehensive test suite
├── scripts/              # Utility scripts for data management
├── logs/                 # Execution and trade logs
└── pyproject.toml        # Project dependencies and configuration
```

## 📦 Installation

### Prerequisites

- Python 3.11+
- [Git](https://git-scm.com/)

### Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ctmes/WeatherEdge.git
   cd WeatherEdge
   ```

2. **Install dependencies**:
   ```bash
   pip install .
   ```
   *For development:*
   ```bash
   pip install -e ".[dev]"
   ```
   *For dashboard:*
   ```bash
   pip install -e ".[dashboard]"
   ```

3. **Environment setup**:
   Create a `.env` file in the root (see `.env.example` for required variables).

## 💻 Usage

The `weatheredge` command is your primary entry point.

### Running a Scan
Run a single scan to find current tradeable signals:
```bash
weatheredge scan --bankroll 10000 -v
```

### Diagnostics
See exactly why markets are being included or excluded:
```bash
weatheredge diagnose --bankroll 5000
```

### Starting the Scheduler
Run the paper-trading loop (scans every 30 minutes by default):
```bash
weatheredge run --bankroll 10000
```

### Calibration
Build station calibration from historical data (requires data from `weatheredge run` history):
```bash
weatheredge calibrate NZWN --days 90
```

### Dashboard
Launch the visualization dashboard:
```bash
weatheredge dashboard
```

### Backtesting
Replay a historical period to evaluate the strategy:
```bash
weatheredge backtest --start 2024-01-01 --end 2024-03-01 --bankroll 10000 --mode full_sim
```

## 🧪 Testing

Run the test suite with `pytest`:
```bash
pytest
```
*Note: Use `-m "not integration"` to skip tests that require live API calls.*

---

*Disclaimer: This is an experimental trading bot. Use at your own risk. Past performance does not guarantee future results.*
