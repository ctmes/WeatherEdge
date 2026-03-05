from __future__ import annotations
import re

# Mirror the code from polymarket.py
WEATHER_KEYWORDS = [
    "temperature", "high temp", "low temp", "precipitation", "rainfall",
    "weather", "temp", "rain", "snow", "heat", "cold", "climate",
    "forecast", "wind", "storm", "hurricane", "tornado", "blizzard",
    "hail", "frost", "drought", "flood", "celsius", "fahrenheit"
]
CLIMATE_KEYWORDS = ["temperature increase", "hottest on record", "hottest month", "hottest year"]
NON_WEATHER_BLOCKLIST = ["election", "president", "crypto", "bitcoin", "war"] # Simplified

def _is_weather_market(item: dict) -> bool:
    title = (item.get("question", "") or "").lower()
    description = (item.get("description", "") or "").lower()
    group_title = (item.get("groupItemTitle", "") or "").lower()
    full_text = f"{title} {description} {group_title}"
    for kw in WEATHER_KEYWORDS + CLIMATE_KEYWORDS:
        if kw in full_text:
            if kw in ("rain", "temp", "wind", "ice", "cold", "heat"):
                if not re.search(rf"\b{kw}\b", full_text):
                    continue
            for block in NON_WEATHER_BLOCKLIST:
                if block in full_text:
                    return False
            return True
    return False

def _extract_city_from_title(title: str) -> str | None:
    match = re.search(
        r"temperature\sin\s+([^?]+?)\s+(?:on|be|reach|stay)\b",
        title,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return None

samples = [
    {"question": "Highest temperature in NYC on March 4?", "description": "Resolves to daily high."},
    {"question": "Highest temperature in London on March 4?", "description": "Resolves to daily high."},
    {"question": "Will the minimum Arctic sea ice extent be reached?", "description": "Arctic sea ice."},
]

for s in samples:
    is_w = _is_weather_market(s)
    city = _extract_city_from_title(s["question"])
    print(f"Q: {s['question']}")
    print(f"  Weather: {is_w}, City: {city}")
