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
    "forecast",
    "wind",
    "storm",
    "hurricane",
    "tornado",
    "blizzard",
    "hail",
    "frost",
    "drought",
    "flood",
    "celsius",
    "fahrenheit",
]

# Blocklist — if ANY of these appear in the market text, reject it even if
# a weather keyword matched.  Prevents false-positives like "political
# climate", "heated debate", "cold war", "crypto winter", etc.
NON_WEATHER_BLOCKLIST: list[str] = [
    # Politics / government
    "election", "president", "congress", "senate", "political",
    "trump", "biden", "democrat", "republican", "gop",
    "vote", "ballot", "poll ", "impeach", "governor", "mayor",
    "legislation", "partisan", "geopolitic",
    # Crypto / finance
    "crypto", "bitcoin", "ethereum", "token price", "nft",
    "stock", "nasdaq", "s&p", "dow jones", "fed rate",
    "interest rate", "gdp", "inflation rate", "unemployment",
    "earnings", "ipo", "market cap",
    # Military / conflict
    "war", "conflict", "military", "invasion", "nuclear",
    "cold war", "sanctions",
    # Sports
    "super bowl", "nfl", "nba", "nhl", "mlb", "fifa",
    "world cup", "championship", "playoff", "touchdown",
    # Entertainment / culture
    "oscar", "grammy", "emmy", "box office", "album",
    # Tech / AI
    "artificial intelligence", "chatgpt", "openai",
    # Health
    "covid", "pandemic", "vaccine", "virus",
    # Misc non-weather
    "approval rating", "indictment", "verdict", "lawsuit",
    "streamer", "twitch", "youtube",
]


# ---------------------------------------------------------------------------
# pmxt Archive
# ---------------------------------------------------------------------------

PMXT_ARCHIVE_BASE = "https://r2.pmxt.dev"

# Slug / title fragments used to identify weather markets in pmxt archive data.
# Applied case-insensitively against market_slug and title columns.
PMXT_WEATHER_SLUGS: list[str] = [
    "temperature", "temp", "rain", "precip", "snow", "weather",
    "heat", "cold", "frost", "wind", "storm", "hurricane",
    "tornado", "blizzard", "hail", "drought", "flood",
    "celsius", "fahrenheit", "highest-temperature", "lowest-temperature",
]


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------

OPEN_METEO_API_KEY = os.getenv("OPEN_METEO_API_KEY")

if OPEN_METEO_API_KEY:
    OPEN_METEO_BASE = "https://customer-api.open-meteo.com/v1/forecast"
    OPEN_METEO_HISTORICAL = "https://customer-historical-forecast-api.open-meteo.com/v1/forecast"
else:
    OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
    OPEN_METEO_HISTORICAL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

OPEN_METEO_MODELS: list[str] = ["ecmwf_ifs", "gfs_seamless", "gem_seamless", "bom_access_global"]
FORECAST_DAYS: int = 7

# typical public release times in UTC (hours, minutes) for NWP models.
# bots usually frontrun these seconds after they arrive at Open-Meteo/NOMADS.
MODEL_RELEASE_CHART = {
    "ecmwf_ifs": {
        "00": (6, 50),
        "12": (18, 50),
    },
    "gfs_seamless": {
        "00": (3, 45),
        "06": (9, 45),
        "12": (15, 45),
        "18": (21, 45),
    }
}


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
