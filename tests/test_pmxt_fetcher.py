"""
Tests for the pmxt archive fetcher — URL generation, weather filtering,
market extraction, and disk caching.

External HTTP calls are mocked to avoid dependency on archive availability.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import polars as pl
import pytest


# =========================================================================
# URL generation
# =========================================================================


class TestBuildDateUrls:
    def test_single_day(self):
        from weatheredge.pmxt_fetcher import build_date_urls

        urls = build_date_urls(date(2026, 3, 3), date(2026, 3, 3))
        assert len(urls) == 1
        assert urls[0][0] == date(2026, 3, 3)
        assert urls[0][1] == "https://r2.pmxt.dev/polymarket_orderbook_2026-03-03T12.parquet"

    def test_multi_day_range(self):
        from weatheredge.pmxt_fetcher import build_date_urls

        urls = build_date_urls(date(2026, 3, 1), date(2026, 3, 3))
        assert len(urls) == 3
        assert "2026-03-01T12" in urls[0][1]
        assert "2026-03-02T12" in urls[1][1]
        assert "2026-03-03T12" in urls[2][1]

    def test_cross_month_boundary(self):
        from weatheredge.pmxt_fetcher import build_date_urls

        urls = build_date_urls(date(2026, 2, 28), date(2026, 3, 1))
        assert len(urls) == 2
        assert "2026-02-28" in urls[0][1]
        assert "2026-03-01" in urls[1][1]

    def test_empty_range(self):
        from weatheredge.pmxt_fetcher import build_date_urls

        # end before start → empty
        urls = build_date_urls(date(2026, 3, 5), date(2026, 3, 3))
        assert len(urls) == 0

    def test_hourly_urls(self):
        from weatheredge.pmxt_fetcher import build_hourly_urls

        urls = build_hourly_urls(date(2026, 3, 3))
        assert len(urls) == 24
        assert "T00.parquet" in urls[0]
        assert "T23.parquet" in urls[23]


# =========================================================================
# Weather slug detection
# =========================================================================


class TestWeatherSlugDetection:
    def test_temperature_slug(self):
        from weatheredge.pmxt_fetcher import _is_weather_slug
        assert _is_weather_slug("highest-temperature-in-nyc") is True

    def test_rain_slug(self):
        from weatheredge.pmxt_fetcher import _is_weather_slug
        assert _is_weather_slug("will-it-rain-in-london") is True

    def test_non_weather_slug(self):
        from weatheredge.pmxt_fetcher import _is_weather_slug
        assert _is_weather_slug("will-trump-win-election") is False

    def test_political_climate_blocked(self):
        from weatheredge.pmxt_fetcher import _is_weather_slug
        # "climate" is a weather keyword, but "political" triggers blocklist
        assert _is_weather_slug("political-climate-shift") is False


# =========================================================================
# Disk cache
# =========================================================================


class TestPmxtCache:
    def test_cache_write_and_read(self, tmp_path):
        import weatheredge.pmxt_fetcher as pf
        original = pf._CACHE_DIR
        pf._CACHE_DIR = tmp_path

        try:
            df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
            pf._write_cache("test.parquet", df)

            result = pf._read_cache("test.parquet")
            assert result is not None
            assert len(result) == 3
            assert result["a"].to_list() == [1, 2, 3]
        finally:
            pf._CACHE_DIR = original

    def test_cache_miss(self, tmp_path):
        import weatheredge.pmxt_fetcher as pf
        original = pf._CACHE_DIR
        pf._CACHE_DIR = tmp_path

        try:
            result = pf._read_cache("nonexistent.parquet")
            assert result is None
        finally:
            pf._CACHE_DIR = original


# =========================================================================
# Orderbook enrichment
# =========================================================================


class TestEnrichMarketWithOrderbook:
    def test_binary_market_enrichment(self):
        from weatheredge.pmxt_fetcher import enrich_market_with_orderbook
        from weatheredge.models import ParsedMarket

        parsed = ParsedMarket(
            condition_id="mkt-001",
            question="Will it rain?",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],  # post-resolution prices
            token_ids=["tok1", "tok2"],
            volume=5000,
            end_date="2026-03-05",
            description="Rain prediction",
        )

        orderbook = {
            "best_bid": 0.65,
            "best_ask": 0.70,
            "side": "YES",
        }

        result = enrich_market_with_orderbook(parsed, orderbook)
        # Midpoint = (0.65 + 0.70) / 2 = 0.675
        assert abs(result.outcome_prices[0] - 0.675) < 0.01
        assert abs(result.outcome_prices[1] - 0.325) < 0.01

    def test_no_price_data_unchanged(self):
        from weatheredge.pmxt_fetcher import enrich_market_with_orderbook
        from weatheredge.models import ParsedMarket

        parsed = ParsedMarket(
            condition_id="mkt-002",
            question="Will it snow?",
            outcomes=["Yes", "No"],
            outcome_prices=[0.3, 0.7],
            token_ids=["tok1", "tok2"],
            volume=3000,
            end_date="2026-03-06",
            description="Snow prediction",
        )

        orderbook = {"best_bid": 0, "best_ask": 0}

        result = enrich_market_with_orderbook(parsed, orderbook)
        # Should be unchanged
        assert result.outcome_prices == [0.3, 0.7]

    def test_no_side_enrichment(self):
        from weatheredge.pmxt_fetcher import enrich_market_with_orderbook
        from weatheredge.models import ParsedMarket

        parsed = ParsedMarket(
            condition_id="mkt-003",
            question="Temperature above 80F?",
            outcomes=["Yes", "No"],
            outcome_prices=[0.5, 0.5],
            token_ids=["tok1", "tok2"],
            volume=8000,
            end_date="2026-03-07",
            description="Temp check",
        )

        # NO side means price is for No → invert for Yes
        orderbook = {"best_bid": 0.40, "best_ask": 0.45, "side": "NO"}

        result = enrich_market_with_orderbook(parsed, orderbook)
        midpoint = (0.40 + 0.45) / 2  # 0.425
        yes_price = 1.0 - midpoint  # 0.575
        assert abs(result.outcome_prices[0] - yes_price) < 0.01


# =========================================================================
# Cache key determinism
# =========================================================================


class TestCacheKey:
    def test_deterministic(self):
        from weatheredge.pmxt_fetcher import _cache_key
        k1 = _cache_key("weather_ticks", "2026-03-03")
        k2 = _cache_key("weather_ticks", "2026-03-03")
        assert k1 == k2
        assert k1.endswith(".parquet")

    def test_different_inputs_different_keys(self):
        from weatheredge.pmxt_fetcher import _cache_key
        k1 = _cache_key("weather_ticks", "2026-03-03")
        k2 = _cache_key("weather_ticks", "2026-03-04")
        assert k1 != k2
