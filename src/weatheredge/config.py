"""
Central configuration — constants from the godpaper, environment loading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{_PROJECT_ROOT / 'weatheredge.db'}",
)


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------

POLYMARKET_CLOB_BASE = "https://clob.polymarket.com"
POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"

# Keywords used to filter weather markets from the full market list
WEATHER_KEYWORDS: list[str] = [
    "temperature",
    "high temp",
    "low temp",
    "precipitation",
    "rainfall",
    "weather",
    "temp",
    "rain",
    "snow",
    "heat",
    "cold",
    "climate",
]


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HISTORICAL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
OPEN_METEO_MODELS: list[str] = ["ecmwf_ifs", "gfs_seamless", "gem_seamless"]
FORECAST_DAYS: int = 7


# ---------------------------------------------------------------------------
# Trading parameters (godpaper Section 10)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradingConfig:
    """Immutable set of risk / sizing parameters."""

    max_single_market_exposure: float = 0.10   # 10% bankroll per market cluster
    max_total_exposure: float = 0.60           # 60% bankroll deployed at once
    min_edge_threshold: float = 0.05            # 5% minimum edge before betting
    min_liquidity: float = 5_000                # $5,000 minimum market volume
    polymarket_fee: float = 0.02                # 2% fee on winning trades
    assumed_slippage: float = 0.03              # 3% penalty when crossing bid/ask spread
    kelly_fraction: float = 0.25                # 25% fractional Kelly
    kelly_max_bet: float = 0.10                 # 10% bankroll cap per cluster
    stop_loss_pct: float = 0.20                 # 20% adverse move → exit

    # Black-swan protection
    model_divergence_no_bet: float = 3.0        # °C — no bet if GFS/ECMWF differ by more
    ensemble_spread_reduce: float = 4.0         # °C — reduce sizing by 50%
    ensemble_spread_no_bet: float = 6.0         # °C — no bet


TRADING = TradingConfig()
