"""
SQLAlchemy ORM models matching the godpaper Section 9 schema,
plus lightweight Pydantic-style dataclasses for in-memory pipeline objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.sqlite import JSON  # portable; works with PG too
from sqlalchemy.orm import DeclarativeBase, relationship


# =========================================================================
# ORM Base
# =========================================================================

class Base(DeclarativeBase):
    pass


# =========================================================================
# Tables  (godpaper Section 9)
# =========================================================================

class Market(Base):
    """Markets scraped from Polymarket."""

    __tablename__ = "markets"

    id = Column(String, primary_key=True)           # Polymarket condition_id
    question = Column(Text)
    station_id = Column(String, index=True)          # Extracted, e.g. NZWN
    metric = Column(String)                          # tmax, tmin, precip
    resolution_date = Column(Date)
    timezone = Column(String)
    bins = Column(JSON)                              # [{low, high}, ...]
    outcomes = Column(JSON)                          # ["Under 18", "18-20", ...]
    outcome_prices = Column(JSON)                    # Gamma outcomePrices (implied probs)
    token_ids = Column(JSON)                         # Polymarket token IDs per outcome
    volume = Column(Float, default=0)
    end_date = Column(DateTime)
    description = Column(Text)
    resolved_outcome = Column(String, nullable=True) # Winning outcome label from Gamma
    created_at = Column(DateTime, server_default=func.now())

    prices = relationship("MarketPrice", back_populates="market")
    signals = relationship("Signal", back_populates="market")


class MarketPrice(Base):
    """Current and historical market prices."""

    __tablename__ = "market_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.id"), index=True)
    prices = Column(JSON)                            # {bin_index: implied_prob}
    recorded_at = Column(DateTime, server_default=func.now())

    market = relationship("Market", back_populates="prices")


class Forecast(Base):
    """Forecast snapshots per station per model run."""

    __tablename__ = "forecasts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(String, index=True)
    model = Column(String)                           # ecmwf, gfs, graphcast
    run_time = Column(DateTime)
    valid_date = Column(Date)
    tmax_mean = Column(Float)
    tmax_std = Column(Float)
    tmin_mean = Column(Float)
    tmin_std = Column(Float)
    precip_mean = Column(Float)
    created_at = Column(DateTime, server_default=func.now())


class StationCalibration(Base):
    """Station calibration data (godpaper Section 6)."""

    __tablename__ = "station_calibration"

    station_id = Column(String, primary_key=True)
    latitude = Column(Float)
    longitude = Column(Float)
    elevation = Column(Float)
    tmax_bias_summer = Column(Float, default=0)
    tmax_bias_winter = Column(Float, default=0)
    tmax_bias_spring = Column(Float, default=0)
    tmax_bias_autumn = Column(Float, default=0)
    tmin_bias_summer = Column(Float, default=0)
    tmin_bias_winter = Column(Float, default=0)
    tmin_bias_spring = Column(Float, default=0)
    tmin_bias_autumn = Column(Float, default=0)
    tmax_rmse = Column(Float, default=0)
    tmin_rmse = Column(Float, default=0)
    tmax_p_values = Column(JSON)
    tmin_p_values = Column(JSON)
    lead_time_degradation = Column(Float, default=0.15)
    last_updated = Column(DateTime)


class Signal(Base):
    """Trade signals — paper and real."""

    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.id"), index=True)
    backtest_run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=True, index=True)
    bin_index = Column(Integer)
    model_prob = Column(Float)
    market_prob = Column(Float)
    slippage = Column(Float, default=0.0)
    edge = Column(Float)
    kelly_fraction = Column(Float)
    bet_size_usd = Column(Float)
    regime_flags = Column(JSON, nullable=True)        # e.g. {"stale_data": True}
    confidence_multiplier = Column(Float, default=1.0)
    signal_type = Column(String, default="paper")    # paper | real | backtest
    created_at = Column(DateTime, server_default=func.now())

    market = relationship("Market", back_populates="signals")
    backtest_run = relationship("BacktestRun", back_populates="signals")
    result = relationship("Result", back_populates="signal", uselist=False)


class Result(Base):
    """Trade results for performance tracking."""

    __tablename__ = "results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, ForeignKey("signals.id"), unique=True)
    backtest_run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=True, index=True)
    resolved_bin = Column(Integer)
    won = Column(Boolean)
    pnl_usd = Column(Float)
    actual_fill_price = Column(Float, nullable=True)
    actual_slippage = Column(Float, nullable=True)
    resolved_at = Column(DateTime)

    signal = relationship("Signal", back_populates="result")
    backtest_run = relationship("BacktestRun", back_populates="results")


class BacktestRun(Base):
    """A single backtester execution — stores config for reproducibility."""

    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    config = Column(JSON, nullable=False)            # full BacktestConfig snapshot
    cache_fingerprint = Column(String, nullable=True) # hash of cached HTTP responses
    markets_count = Column(Integer, default=0)
    signals_count = Column(Integer, default=0)
    total_pnl = Column(Float, nullable=True)
    avg_brier = Column(Float, nullable=True)
    avg_rps = Column(Float, nullable=True)
    avg_log_score = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)

    signals = relationship("Signal", back_populates="backtest_run")
    results = relationship("Result", back_populates="backtest_run")
    brier_scores = relationship("BrierScore", back_populates="backtest_run")


class PeakBankroll(Base):
    """Single-row table storing the historical peak bankroll for drawdown tracking."""

    __tablename__ = "peak_bankroll"

    id = Column(Integer, primary_key=True, default=1)
    peak_usd = Column(Float, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BrierScore(Base):
    """Brier scores per model per market."""

    __tablename__ = "brier_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=True, index=True)
    market_id = Column(String, index=True)
    model = Column(String)
    brier_score = Column(Float)
    baseline_brier = Column(Float, nullable=True)    # For BSS calculation
    rps = Column(Float, nullable=True)               # Ranked Probability Score (ordinal-aware)
    log_score = Column(Float, nullable=True)         # Ignorance score (Kelly-linked)
    calculated_at = Column(DateTime, server_default=func.now())

    backtest_run = relationship("BacktestRun", back_populates="brier_scores")


# =========================================================================
# Lightweight in-memory data objects (no DB dependency)
# =========================================================================

@dataclass
class ParsedMarket:
    """A Polymarket weather market with resolution rules parsed out."""

    condition_id: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    token_ids: list[str]
    volume: float
    end_date: str
    description: str

    # Parsed from description
    station_id: str | None = None
    metric: str | None = None          # tmax, tmin, precip
    bins: list[tuple[float, float]] = field(default_factory=list)
    timezone: str | None = None
    resolution_date: date | None = None


@dataclass
class ForecastResult:
    """Ensemble forecast for a single station on a single date."""

    station_id: str
    valid_date: date
    tmax_mean: float
    tmax_std: float
    tmin_mean: float | None = None
    tmin_std: float | None = None
    precip_mean: float | None = None
    models_used: list[str] = field(default_factory=list)
    # Absolute difference between ECMWF and GFS tmax means (°C).
    # 0.0 when only one model is available or when using the ensemble endpoint.
    # Used by regime.assess_regime to fire the MODEL_DIVERGENCE_HIGH guard.
    model_divergence: float = 0.0
    # 90th–10th percentile tmax spread across ensemble members (°C).
    # Populated by _fetch_ensemble; used by the pipeline instead of tmax_std
    # so the regime circuit-breaker thresholds mean what they say.
    ensemble_spread: float = 0.0
    # Number of ensemble members that contributed to this forecast.
    ensemble_member_count: int = 1


@dataclass
class EdgeSignal:
    """Calculated edge for one bin of one market."""

    market_id: str
    question: str
    station_id: str
    bin_index: int
    bin_label: str
    bin_range: tuple[float, float]
    model_prob: float
    market_prob: float
    raw_edge: float
    net_edge: float
    kelly_fraction: float
    recommended_bet_usd: float
    regime_flags: list[str] = field(default_factory=list)
    confidence_multiplier: float = 1.0
    slippage: float = 0.0
