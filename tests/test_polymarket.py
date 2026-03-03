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
