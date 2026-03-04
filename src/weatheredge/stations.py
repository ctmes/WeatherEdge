"""
Station lookup table — maps station IDs to coordinates, timezone,
and microclimate notes.  From godpaper Section 13 + meteorologist persona.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    """Metadata for a single weather resolution station."""

    id: str
    name: str
    city: str
    country: str
    latitude: float
    longitude: float
    elevation_m: float
    timezone: str
    preferred_unit: str = "C"  # "C" or "F"
    notes: str = ""


# ---------------------------------------------------------------------------
# Priority stations from godpaper Section 13
# ---------------------------------------------------------------------------

STATIONS: dict[str, Station] = {
    "NZWN": Station(
        id="NZWN",
        name="Wellington Airport",
        city="Wellington",
        country="NZ",
        latitude=-41.3276,
        longitude=174.8050,
        elevation_m=12,
        timezone="Pacific/Auckland",
        preferred_unit="C",
        notes=(
            "Coastal Cook Strait exposure. Southerly Buster risk — sudden 5-10°C drops. "
            "Northerly foehn can push 3-5°C above city. Wind direction is the key variable."
        ),
    ),
    "EGLL": Station(
        id="EGLL",
        name="London Heathrow",
        city="London",
        country="UK",
        latitude=51.4700,
        longitude=-0.4543,
        elevation_m=25,
        timezone="Europe/London",
        preferred_unit="C",
        notes=(
            "Urban heat island +1-2°C vs countryside. "
            "ECMWF trained heavily on UK data — bias may be partially corrected. "
            "Fog frequency in autumn/winter suppresses maxima."
        ),
    ),
    "KJFK": Station(
        id="KJFK",
        name="JFK International",
        city="New York",
        country="US",
        latitude=40.6413,
        longitude=-73.7781,
        elevation_m=4,
        timezone="America/New_York",
        preferred_unit="F",
        notes="Coastal station, sea breeze influence. High volume market.",
    ),
    "KLGA": Station(
        id="KLGA",
        name="LaGuardia Airport",
        city="New York",
        country="US",
        latitude=40.7772,
        longitude=-73.8726,
        elevation_m=6,
        timezone="America/New_York",
        preferred_unit="F",
        notes=(
            "Polymarket NYC resolution station. Waterfront exposure on Flushing Bay. "
            "Urban heat island effect. Slightly warmer than JFK due to less open water."
        ),
    ),
    "KNYC": Station(
        id="KNYC",
        name="Central Park",
        city="New York",
        country="US",
        latitude=40.7789,
        longitude=-73.9692,
        elevation_m=47,
        timezone="America/New_York",
        preferred_unit="F",
        notes="Urban heat island. Often used as alternate NYC resolution station.",
    ),
    "KORD": Station(
        id="KORD",
        name="O'Hare International",
        city="Chicago",
        country="US",
        latitude=41.9742,
        longitude=-87.9073,
        elevation_m=205,
        timezone="America/Chicago",
        preferred_unit="F",
        notes=(
            "Lake Michigan influence — lake breeze holds temps 3-5°C lower on warm days. "
            "Winter arctic outbreaks. Highest temperature variance of major US stations."
        ),
    ),
    "YSSY": Station(
        id="YSSY",
        name="Sydney Airport",
        city="Sydney",
        country="AU",
        latitude=-33.9461,
        longitude=151.1772,
        elevation_m=6,
        timezone="Australia/Sydney",
        preferred_unit="C",
        notes=(
            "Botany Bay sea breeze typically 11am-2pm in summer, capping afternoon temps. "
            "Westerly wind days are hottest — sea breeze blocked. "
            "850hPa westerly >15kt usually means sea breeze failure. Fog risk winter mornings."
        ),
    ),
    "YMML": Station(
        id="YMML",
        name="Melbourne Airport",
        city="Melbourne",
        country="AU",
        latitude=-37.6690,
        longitude=144.8410,
        elevation_m=132,
        timezone="Australia/Melbourne",
        preferred_unit="C",
        notes=(
            "Most variable temperature of any AU capital. 'Four seasons in one day.' "
            "Cool change timing is critical — 2pm vs 5pm means 5-8°C difference. "
            "Highest variance — needs wider probability distributions."
        ),
    ),
    "RJTT": Station(
        id="RJTT",
        name="Tokyo Haneda",
        city="Tokyo",
        country="JP",
        latitude=35.5494,
        longitude=139.7798,
        elevation_m=6,
        timezone="Asia/Tokyo",
        preferred_unit="C",
        notes=(
            "Strong urban heat island. Monsoon season Jun-Sep convective uncertainty high. "
            "Typhoon season Jul-Oct — no trade during active threats. "
            "Winter is highly predictable — best betting period."
        ),
    ),
    "KMIA": Station(
        id="KMIA",
        name="Miami International",
        city="Miami",
        country="US",
        latitude=25.7959,
        longitude=-80.2870,
        elevation_m=3,
        timezone="America/New_York",
        preferred_unit="F",
        notes="Tropical maritime climate. Sea breeze convergence zone. Thunderstorm risk year-round.",
    ),
    "CYYZ": Station(
        id="CYYZ",
        name="Toronto Pearson",
        city="Toronto",
        country="CA",
        latitude=43.6777,
        longitude=-79.6248,
        elevation_m=173,
        timezone="America/Toronto",
        preferred_unit="C",
        notes="Lake Ontario moderation. Arctic outbreaks in winter. Lake-effect cloud/snow.",
    ),
    "KSEA": Station(
        id="KSEA",
        name="Seattle-Tacoma International",
        city="Seattle",
        country="US",
        latitude=47.4502,
        longitude=-122.3088,
        elevation_m=130,
        timezone="America/Los_Angeles",
        preferred_unit="F",
        notes="Marine influence from Puget Sound. Rain shadow effects. Mild maritime climate.",
    ),
    "KATL": Station(
        id="KATL",
        name="Hartsfield-Jackson Atlanta",
        city="Atlanta",
        country="US",
        latitude=33.6407,
        longitude=-84.4277,
        elevation_m=313,
        timezone="America/New_York",
        preferred_unit="F",
        notes="Inland subtropical. Urban heat island. Summer convective storms.",
    ),
    "KDFW": Station(
        id="KDFW",
        name="Dallas/Fort Worth International",
        city="Dallas",
        country="US",
        latitude=32.8998,
        longitude=-97.0403,
        elevation_m=185,
        timezone="America/Chicago",
        preferred_unit="F",
        notes="Continental climate. Extreme summer heat. Dryline thunderstorm risk.",
    ),
    "RKSI": Station(
        id="RKSI",
        name="Incheon International",
        city="Seoul",
        country="KR",
        latitude=37.4602,
        longitude=126.4407,
        elevation_m=7,
        timezone="Asia/Seoul",
        preferred_unit="C",
        notes="East Asian monsoon influence. Cold continental winters. Hot humid summers.",
    ),
    "LFPG": Station(
        id="LFPG",
        name="Paris Charles de Gaulle",
        city="Paris",
        country="FR",
        latitude=49.0097,
        longitude=2.5479,
        elevation_m=119,
        timezone="Europe/Paris",
        preferred_unit="C",
        notes="Oceanic climate with continental influence. Urban heat island effect.",
    ),
    "SAEZ": Station(
        id="SAEZ",
        name="Buenos Aires Ezeiza",
        city="Buenos Aires",
        country="AR",
        latitude=-34.8222,
        longitude=-58.5358,
        elevation_m=20,
        timezone="America/Argentina/Buenos_Aires",
        preferred_unit="C",
        notes="Humid subtropical. Pampero wind events. Southern Hemisphere seasons.",
    ),
    "LTAC": Station(
        id="LTAC",
        name="Ankara Esenboga",
        city="Ankara",
        country="TR",
        latitude=40.1281,
        longitude=32.9951,
        elevation_m=953,
        timezone="Europe/Istanbul",
        preferred_unit="C",
        notes="High plateau continental. Cold winters, hot dry summers. Elevation effect.",
    ),
    "SBGR": Station(
        id="SBGR",
        name="Sao Paulo Guarulhos",
        city="Sao Paulo",
        country="BR",
        latitude=-23.4356,
        longitude=-46.4731,
        elevation_m=750,
        timezone="America/Sao_Paulo",
        preferred_unit="C",
        notes="Tropical highland. Afternoon convective rain. Elevation moderates heat.",
    ),
    "KDCA": Station(
        id="KDCA",
        name="Reagan National",
        city="Washington",
        country="US",
        latitude=38.8512,
        longitude=-77.0402,
        elevation_m=4,
        timezone="America/New_York",
        preferred_unit="F",
        notes="River influence. East coast weather patterns. Urban heat island.",
    ),
    "GLOBAL": Station(
        id="GLOBAL",
        name="Global Climate Anomaly",
        city="Global",
        country="Global",
        latitude=0.0,
        longitude=0.0,
        elevation_m=0,
        timezone="UTC",
        preferred_unit="C",
        notes="Mock station for Polymarket monthly/yearly temperature anomaly markets.",
    ),
}


# ---------------------------------------------------------------------------
# City-to-station mapping for Polymarket daily weather markets
# Keys are lowercase city names as they appear in market titles.
# ---------------------------------------------------------------------------

CITY_TO_STATION: dict[str, str] = {
    "nyc": "KLGA",
    "new york": "KLGA",
    "london": "EGLL",
    "chicago": "KORD",
    "wellington": "NZWN",
    "sydney": "YSSY",
    "melbourne": "YMML",
    "tokyo": "RJTT",
    "jfk": "KJFK",
    # Cities from user's screenshot that we add best-guess ICAO for
    "miami": "KMIA",
    "toronto": "CYYZ",
    "seattle": "KSEA",
    "atlanta": "KATL",
    "dallas": "KDFW",
    "seoul": "RKSI",
    "paris": "LFPG",
    "buenos aires": "SAEZ",
    "ankara": "LTAC",
    "sao paulo": "SBGR",
    "dc": "KDCA",
    "washington": "KDCA",
}


def get_station(station_id: str) -> Station | None:
    """Look up a station by ICAO ID (case-insensitive)."""
    return STATIONS.get(station_id.upper())


def find_station_by_city(city: str) -> Station | None:
    """Fuzzy match a city name to a station."""
    city_lower = city.lower()
    for station in STATIONS.values():
        if city_lower in station.city.lower():
            return station
    return None
