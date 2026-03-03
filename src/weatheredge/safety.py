"""
Data validation and safety guards — from the Risk Manager persona.

Prevents stale data, runaway execution, and position limit breaches.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("weatheredge.safety")


# =========================================================================
# Data freshness
# =========================================================================

MAX_DATA_AGE_MINUTES = 15  # Risk Manager: reject data older than 15 min


class StaleDataError(Exception):
    """Raised when data is older than the maximum allowed age."""
    pass


def validate_data_freshness(
    data_timestamp: datetime,
    max_age_minutes: int = MAX_DATA_AGE_MINUTES,
    label: str = "data",
) -> None:
    """
    Reject data older than max_age_minutes.
    Raises StaleDataError if data is too old.
    """
    now = datetime.utcnow()
    age = now - data_timestamp
    age_minutes = age.total_seconds() / 60

    if age_minutes > max_age_minutes:
        raise StaleDataError(
            f"{label} is {age_minutes:.1f} minutes old "
            f"(max allowed: {max_age_minutes} min)"
        )


# =========================================================================
# Execution guards (Risk Manager Section 4)
# =========================================================================

class ExecutionRateLimitError(Exception):
    """Raised when execution rate limits are breached."""
    pass


@dataclass
class ExecutionGuard:
    """
    Prevents runaway execution bugs and position concentration.

    From Risk Manager persona:
    - Max 5 orders per minute
    - Max 3 orders per market per day
    """

    max_orders_per_minute: int = 5
    max_orders_per_market_per_day: int = 3

    _minute_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _daily_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def can_execute(self, market_id: str) -> bool:
        """
        Check whether an order can be placed. Raises if limits breached.
        """
        now = datetime.utcnow()
        minute_key = now.strftime("%Y%m%d%H%M")
        day_key = now.strftime("%Y%m%d")

        # Per-minute rate limit
        if self._minute_counts[minute_key] >= self.max_orders_per_minute:
            raise ExecutionRateLimitError(
                f"Rate limit: {self.max_orders_per_minute} orders/minute exceeded"
            )

        # Per-market daily limit
        market_day_key = f"{market_id}_{day_key}"
        if self._daily_counts[market_day_key] >= self.max_orders_per_market_per_day:
            raise ExecutionRateLimitError(
                f"Market {market_id}: {self.max_orders_per_market_per_day} orders/day exceeded"
            )

        return True

    def record_execution(self, market_id: str) -> None:
        """Record that an order was placed."""
        now = datetime.utcnow()
        minute_key = now.strftime("%Y%m%d%H%M")
        day_key = now.strftime("%Y%m%d")

        self._minute_counts[minute_key] += 1
        self._daily_counts[f"{market_id}_{day_key}"] += 1

    def reset_daily(self) -> None:
        """Reset daily counters (call at start of each trading day)."""
        self._daily_counts.clear()


# =========================================================================
# Position limits (Risk Manager Section 1)
# =========================================================================

@dataclass
class PositionLimits:
    """
    Hard position limits from the Risk Manager persona.
    All values are fractions of bankroll.
    """

    max_single_bin: float = 0.03          # 3% per bin
    max_single_market_cluster: float = 0.08  # 8% per market
    max_correlated_exposure: float = 0.25    # 25% same weather system
    max_total_deployed: float = 0.50         # 50% total
    emergency_reserve: float = 0.20          # 20% never touch


def check_position_limits(
    proposed_bet_usd: float,
    bankroll: float,
    current_market_exposure: float,
    current_total_exposure: float,
    limits: PositionLimits = PositionLimits(),
) -> tuple[bool, str]:
    """
    Check whether a proposed bet violates any position limits.

    Returns (is_allowed, reason).
    """
    # Single bin limit
    if proposed_bet_usd > bankroll * limits.max_single_bin:
        return False, (
            f"Bet ${proposed_bet_usd:.0f} exceeds single bin limit "
            f"(${bankroll * limits.max_single_bin:.0f})"
        )

    # Market cluster limit
    new_market_total = current_market_exposure + proposed_bet_usd
    if new_market_total > bankroll * limits.max_single_market_cluster:
        return False, (
            f"Market exposure ${new_market_total:.0f} exceeds cluster limit "
            f"(${bankroll * limits.max_single_market_cluster:.0f})"
        )

    # Total deployed limit
    new_total = current_total_exposure + proposed_bet_usd
    max_deployable = bankroll * (1 - limits.emergency_reserve) * limits.max_total_deployed / (1 - limits.emergency_reserve)
    if new_total > bankroll * limits.max_total_deployed:
        return False, (
            f"Total exposure ${new_total:.0f} exceeds deployment limit "
            f"(${bankroll * limits.max_total_deployed:.0f})"
        )

    return True, "OK"


# =========================================================================
# Circuit breakers (Risk Manager Section 1 Layer 3)
# =========================================================================

@dataclass
class CircuitBreaker:
    """
    Automated loss triggers that pause or stop trading.
    """

    daily_loss_limit: float = 0.05      # 5% → pause 24h
    weekly_loss_limit: float = 0.10     # 10% → full review
    monthly_loss_limit: float = 0.15    # 15% → system shutdown
    drawdown_limit: float = 0.25        # 25% from peak → strategy review

    def check(
        self,
        bankroll: float,
        daily_pnl: float,
        weekly_pnl: float,
        monthly_pnl: float,
        peak_bankroll: float,
    ) -> tuple[str, str]:
        """
        Check circuit breakers. Returns (status, message).
        Status: "OK", "PAUSE", "REVIEW", "SHUTDOWN"
        """
        drawdown = (peak_bankroll - bankroll) / peak_bankroll if peak_bankroll > 0 else 0

        if drawdown >= self.drawdown_limit:
            return "SHUTDOWN", (
                f"Cumulative drawdown {drawdown:.1%} from peak "
                f"${peak_bankroll:.0f} → ${bankroll:.0f}"
            )

        if abs(monthly_pnl) / bankroll >= self.monthly_loss_limit and monthly_pnl < 0:
            return "SHUTDOWN", (
                f"Monthly loss {monthly_pnl / bankroll:.1%} "
                f"exceeds {self.monthly_loss_limit:.0%} limit"
            )

        if abs(weekly_pnl) / bankroll >= self.weekly_loss_limit and weekly_pnl < 0:
            return "REVIEW", (
                f"Weekly loss {weekly_pnl / bankroll:.1%} "
                f"exceeds {self.weekly_loss_limit:.0%} limit"
            )

        if abs(daily_pnl) / bankroll >= self.daily_loss_limit and daily_pnl < 0:
            return "PAUSE", (
                f"Daily loss {daily_pnl / bankroll:.1%} "
                f"exceeds {self.daily_loss_limit:.0%} limit"
            )

        return "OK", "All circuit breakers clear"


# =========================================================================
# System health checks
# =========================================================================

@dataclass
class HealthCheck:
    """Aggregated system health status."""

    polymarket_api: bool = False
    forecast_api: bool = False
    database: bool = False
    data_fresh: bool = False
    circuit_breakers_clear: bool = False
    all_healthy: bool = False

    def summary(self) -> str:
        checks = {
            "Polymarket API": self.polymarket_api,
            "Forecast API": self.forecast_api,
            "Database": self.database,
            "Data Freshness": self.data_fresh,
            "Circuit Breakers": self.circuit_breakers_clear,
        }
        lines = []
        for name, ok in checks.items():
            status = "✅" if ok else "❌"
            lines.append(f"  {status} {name}")
        return "\n".join(lines)
