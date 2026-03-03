"""
Tests for weatheredge.polymarket — resolution rule parsing and bin extraction.
"""

import pytest

from weatheredge.polymarket import parse_bins_from_outcomes, parse_resolution_rules


# =========================================================================
# parse_resolution_rules
# =========================================================================

class TestParseResolutionRules:
    """Extract station ID, metric, and timezone from description text."""

    def test_extracts_icao_station(self):
        desc = "Resolves based on Weather Underground station NZWN daily high."
        result = parse_resolution_rules(desc)
        assert result["station_id"] == "NZWN"

    def test_extracts_metric_tmax(self):
        desc = "What will the high temperature be?"
        result = parse_resolution_rules(desc)
        assert result["metric"] == "tmax"

    def test_extracts_metric_tmin(self):
        desc = "What will the low temperature be?"
        result = parse_resolution_rules(desc)
        assert result["metric"] == "tmin"

    def test_extracts_metric_precip(self):
        desc = "Total precipitation for the day."
        result = parse_resolution_rules(desc)
        assert result["metric"] == "precip"

    def test_extracts_timezone(self):
        desc = "Resolution period is in NZST timezone."
        result = parse_resolution_rules(desc)
        assert result["timezone"] == "NZST"

    def test_wu_station_reference(self):
        desc = "This market resolves using data from Weather Underground station KORD."
        result = parse_resolution_rules(desc)
        assert result["station_id"] == "KORD"

    def test_prefers_known_icao_over_random(self):
        desc = "Measured at station EGLL near the CITY area."
        result = parse_resolution_rules(desc)
        assert result["station_id"] == "EGLL"

    def test_date_extraction(self):
        question = "What will the high temperature in Wellington on March 5 be?"
        result = parse_resolution_rules("", question)
        assert result["resolution_date"] is not None
        assert result["resolution_date"].month == 3
        assert result["resolution_date"].day == 5

    def test_empty_text_returns_nones(self):
        result = parse_resolution_rules("")
        assert result["station_id"] is None
        assert result["metric"] is None


# =========================================================================
# parse_bins_from_outcomes
# =========================================================================

class TestParseBinsFromOutcomes:
    """Convert outcome labels to (low, high) bin tuples."""

    def test_standard_bins(self):
        outcomes = ["Under 18", "18-20", "20-22", "22-24", "Over 24"]
        bins = parse_bins_from_outcomes(outcomes)
        assert len(bins) == 5
        assert bins[0] == (float("-inf"), 18.0)
        assert bins[1] == (18.0, 20.0)
        assert bins[2] == (20.0, 22.0)
        assert bins[3] == (22.0, 24.0)
        assert bins[4] == (24.0, float("inf"))

    def test_decimal_bins(self):
        outcomes = ["Under 15.5", "15.5-17.5", "Over 17.5"]
        bins = parse_bins_from_outcomes(outcomes)
        assert len(bins) == 3
        assert bins[0][1] == 15.5
        assert bins[1] == (15.5, 17.5)

    def test_empty_outcomes(self):
        assert parse_bins_from_outcomes([]) == []

    def test_unparseable_labels_skipped(self):
        outcomes = ["Yes", "No"]
        bins = parse_bins_from_outcomes(outcomes)
        assert bins == []


# =========================================================================
# _raw_to_parsed_market — city-name fallback
# =========================================================================

class TestCityNameFallback:
    """When ICAO/WU parsing fails, fall back to city name from question."""

    def test_city_name_sets_station_id(self):
        from weatheredge.polymarket import _raw_to_parsed_market
        item = {
            "conditionId": "test-nyc-123",
            "question": "Highest temperature in NYC on March 5?",
            "description": "This market resolves to the high temperature.",
            "outcomes": '["42-43°F", "44-45°F"]',
            "outcomePrices": '[0.3, 0.7]',
        }
        market = _raw_to_parsed_market(item)
        assert market is not None
        assert market.station_id == "KLGA"

    def test_unknown_city_leaves_station_none(self):
        from weatheredge.polymarket import _raw_to_parsed_market
        item = {
            "conditionId": "test-timbuktu-456",
            "question": "Highest temperature in Timbuktu on March 5?",
            "description": "Resolves to the daily high.",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '[0.5, 0.5]',
        }
        market = _raw_to_parsed_market(item)
        assert market is not None
        assert market.station_id is None

    def test_non_weather_title_no_crash(self):
        from weatheredge.polymarket import _raw_to_parsed_market
        item = {
            "conditionId": "test-other-789",
            "question": "Will it rain tomorrow?",
            "description": "General weather question.",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '[0.5, 0.5]',
        }
        market = _raw_to_parsed_market(item)
        assert market is not None


# =========================================================================
# fetch_weather_events (mocked)
# =========================================================================

class TestFetchWeatherEvents:
    """Test the /events endpoint fetcher with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_parses_daily_event(self, monkeypatch):
        """A well-formed event with child markets should produce a ParsedMarket."""
        from weatheredge import polymarket as pm

        fake_event = {
            "id": "evt-nyc-1",
            "title": "Highest temperature in NYC on March 5?",
            "endDate": "2026-03-05T23:59:59Z",
            "volume": 10000,
            "markets": [
                {
                    "groupItemTitle": "42-43°F",
                    "groupItemThreshold": "42",
                    "description": "Resolves to the daily high temp in NYC.",
                    "outcomePrices": '[0.2, 0.8]',
                    "outcomes": '["Yes", "No"]',
                },
                {
                    "groupItemTitle": "44°F or higher",
                    "groupItemThreshold": "44",
                    "description": "Resolves to the daily high temp in NYC.",
                    "outcomePrices": '[0.7, 0.3]',
                    "outcomes": '["Yes", "No"]',
                },
            ],
        }

        async def fake_get_json(url, params=None):
            if "/events" in url:
                return [fake_event]
            return []

        monkeypatch.setattr(pm, "_get_json", fake_get_json)

        results = await pm.fetch_weather_events()
        assert len(results) >= 1
        m = results[0]
        assert m.station_id == "KLGA"
        assert m.question == "Highest temperature in NYC on March 5?"

    @pytest.mark.asyncio
    async def test_skips_non_weather_events(self, monkeypatch):
        """Events without temperature keywords should be skipped."""
        from weatheredge import polymarket as pm

        async def fake_get_json(url, params=None):
            return [{"id": "evt-politics", "title": "Will candidate X win?", "markets": []}]

        monkeypatch.setattr(pm, "_get_json", fake_get_json)

        results = await pm.fetch_weather_events()
        assert len(results) == 0
