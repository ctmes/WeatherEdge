"""
Tests for weatheredge.stations — station lookup table.
"""

import pytest

from weatheredge.stations import STATIONS, find_station_by_city, get_station


class TestStationLookup:
    """Verify all priority stations from godpaper Section 13 are present."""

    PRIORITY_IDS = ["NZWN", "EGLL", "KJFK", "KNYC", "KORD", "YSSY", "YMML", "RJTT"]

    def test_all_priority_stations_present(self):
        for sid in self.PRIORITY_IDS:
            station = get_station(sid)
            assert station is not None, f"Station {sid} missing from lookup"

    def test_coordinates_are_valid(self):
        for sid in self.PRIORITY_IDS:
            s = get_station(sid)
            assert -90 <= s.latitude <= 90
            assert -180 <= s.longitude <= 180

    def test_case_insensitive_lookup(self):
        assert get_station("nzwn") is not None
        assert get_station("Nzwn") is not None

    def test_unknown_station_returns_none(self):
        assert get_station("XXXX") is None

    def test_find_by_city(self):
        s = find_station_by_city("Wellington")
        assert s is not None
        assert s.id == "NZWN"

    def test_find_by_city_not_found(self):
        assert find_station_by_city("Atlantis") is None

    def test_all_stations_have_timezone(self):
        for s in STATIONS.values():
            assert s.timezone, f"Station {s.id} has no timezone"
