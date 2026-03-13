"""
Polymarket CLOB API scraper — fetch active weather markets, prices,
and parse resolution rules from description text.
"""

from __future__ import annotations

import logging  # FIX #1: removed duplicate import
import re
from datetime import date, datetime
from typing import Any

import httpx

log = logging.getLogger("weatheredge.polymarket")

from weatheredge.cache import cache_key as _cache_key, read_cache, write_cache
from weatheredge.config import (
    POLYMARKET_CLOB_BASE,
    POLYMARKET_GAMMA_BASE,
    NON_WEATHER_BLOCKLIST,
    WEATHER_KEYWORDS,
    VERIFY_SSL,
)
from weatheredge.models import ParsedMarket

_SUBDIR = "polymarket_cache"

# Additional keywords for global climate anomaly markets
CLIMATE_KEYWORDS = ["temperature increase", "hottest on record", "hottest month", "hottest year"]


# =========================================================================
# API helpers
# =========================================================================

async def _get_json(url: str, params: dict | None = None) -> Any:
    """Async GET → JSON with sensible timeout and SSL verification toggle."""
    try:
        async with httpx.AsyncClient(timeout=30, verify=VERIFY_SSL) as client:
            resp = await client.get(url, params=params or {})
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) and VERIFY_SSL:
            log.error("SSL Verification failed. This is likely due to a hostname mismatch on Polymarket's Gamma API (Azure Front Door).")
            log.error("FIX: Run with `export VERIFY_SSL=false` or set it in your .env file.")
        raise


# =========================================================================
# CLOB token ID enrichment
# =========================================================================

async def _fetch_clob_token_ids(condition_id: str) -> list[str]:
    """
    Look up the real CLOB token IDs for a market using its condition ID.

    The Gamma API sometimes returns empty ``clobTokenIds`` for child markets.
    Falling back to the condition ID as a subscription key is wrong — the
    CLOB WebSocket uses token IDs (one per outcome), not condition IDs.

    ``GET /markets?condition_id=<id>`` returns a list of market dicts each
    with a ``tokens`` array.  We collect the ``token_id`` from every token
    across all matching markets.

    Results are cached for 1 hour since token IDs never change for a given
    condition.
    """
    ck = _cache_key("clob_tokens", condition_id)
    cached = read_cache(_SUBDIR, ck, max_age_hours=60)
    if cached is not None:
        return cached

    url = f"{POLYMARKET_CLOB_BASE}/markets"
    try:
        data = await _get_json(url, {"condition_id": condition_id})

        # Unwrap paginated envelope — CLOB returns {"data": [...], "next_cursor": ...}
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        elif isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or not data:
            return []

        token_ids: list[str] = []
        for market in data:
            # Skip markets with no active order book — the WebSocket will
            # never send events for these regardless of token ID correctness.
            if not market.get("enable_order_book", True):
                log.debug(
                    "CLOB enrichment: skipping market within condition %s "
                    "— order book disabled (closed/resolved market)",
                    condition_id,
                )
                continue
            for token in market.get("tokens", []):
                tid = str(token.get("token_id", "")).strip()
                if tid:
                    token_ids.append(tid)

        if token_ids:
            write_cache(_SUBDIR, ck, token_ids)
            log.debug(
                "CLOB enrichment: resolved %d token IDs for condition %s",
                len(token_ids), condition_id,
            )
        else:
            log.warning(
                "CLOB enrichment: no token IDs found for condition %s", condition_id
            )
        return token_ids

    except Exception as e:
        log.warning(
            "CLOB enrichment: failed to fetch token IDs for condition %s: %s",
            condition_id, e,
        )
        return []


def _looks_like_condition_id(tid: str) -> bool:
    """
    Return True if ``tid`` looks like a condition ID rather than a real
    CLOB token ID.

    Polymarket token IDs are large decimal integers as strings, e.g.:
        "71321045679452639895493894848938929495..."
    Condition IDs are 0x-prefixed hex strings, e.g.:
        "0x3f7d2c1a..."

    When Gamma's ``clobTokenIds`` is empty and the code falls back to using
    the condition ID as a token ID, the subscription silently fails because
    the CLOB WebSocket does not recognise condition IDs as valid token keys.
    """
    tid = tid.strip()
    if not tid:
        return True
    # Condition IDs are always 0x-prefixed hex
    if tid.startswith("0x"):
        return True
    # Real token IDs are all digits (very large integers)
    if not tid.isdigit():
        return True
    return False


async def _enrich_missing_token_ids(markets: list[ParsedMarket]) -> None:
    """
    For every market whose ``token_ids`` appear to contain condition IDs
    rather than real CLOB token IDs, fetch the correct token IDs from the
    CLOB API and replace them in-place.

    A token ID is identified as a probable condition ID (and therefore
    wrong) when it matches the market's own ``condition_id`` — i.e. the
    Gamma fallback path was taken during parsing.

    This is the fix for the 7/9 zero-price issue: without correct token IDs
    the WebSocket subscription is silently ignored by the CLOB server and
    no orderbook events are ever delivered.
    """
    import asyncio

    needs_enrichment = [
        m for m in markets
        if not m.token_ids or any(_looks_like_condition_id(tid) for tid in m.token_ids)
    ]

    if not needs_enrichment:
        return

    log.info(
        "CLOB enrichment: %d/%d markets have missing or fallback token IDs — "
        "fetching real IDs from CLOB API",
        len(needs_enrichment), len(markets),
    )

    async def _enrich_one(market: ParsedMarket) -> None:
        # For event-based markets (daily weather) the token_ids list stores
        # per-bin child token IDs. We need to enrich each child individually
        # using the condition_id stored in the CHILD_IDS footer of description.
        child_ids = _extract_child_ids_from_description(market.description)

        if child_ids:
            # Fetch token IDs for every child condition and flatten
            results = await asyncio.gather(
                *[_fetch_clob_token_ids(cid) for cid in child_ids],
                return_exceptions=True,
            )
            new_token_ids: list[str] = []
            for r in results:
                if isinstance(r, list):
                    # Take only the YES token (index 0) per child market
                    if r:
                        new_token_ids.append(r[0])
            if new_token_ids:
                market.token_ids = new_token_ids
                log.info(
                    "CLOB enrichment: market %s updated with %d child token IDs",
                    market.condition_id, len(new_token_ids),
                )
        else:
            # Single-outcome market — look up by the market's own condition ID
            token_ids = await _fetch_clob_token_ids(market.condition_id)
            if token_ids:
                market.token_ids = token_ids
                log.info(
                    "CLOB enrichment: market %s updated with %d token IDs",
                    market.condition_id, len(token_ids),
                )

    await asyncio.gather(*[_enrich_one(m) for m in needs_enrichment])


def _extract_child_ids_from_description(description: str) -> list[str]:
    """
    Extract child condition IDs from the ``CHILD_IDS:`` footer appended by
    ``parse_daily_weather_event``.

    Returns an empty list if no footer is present.
    """
    match = re.search(r"CHILD_IDS:([^\n]+)", description or "")
    if not match:
        return []
    return [cid.strip() for cid in match.group(1).split(",") if cid.strip()]


# =========================================================================
# Market fetching
# =========================================================================

async def fetch_weather_markets(
    *,
    max_pages: int = 50,
    page_size: int = 100,
) -> list[ParsedMarket]:
    """
    Fetch active weather markets from both the Gamma ``/markets`` and
    ``/events`` endpoints, merge and de-duplicate.

    The ``/events`` endpoint returns parent events with child markets
    (one per temperature bin), which is the format Polymarket uses for
    daily weather markets.  The ``/markets`` endpoint returns individual
    child markets that may lack the parent context needed for station
    matching.

    Event-based results take priority during de-duplication since they
    carry richer metadata (city, bins, station mapping).
    """
    # --- Fetch from /events first (higher quality data) ---
    event_markets = await fetch_weather_events(max_pages=max_pages, page_size=page_size)
    seen_ids: set[str] = {m.condition_id for m in event_markets}
    log.info("Fetched %d markets from /events endpoint", len(event_markets))

    # --- Fetch from /markets as fallback ---
    market_markets: list[ParsedMarket] = []
    offset = 0

    for _ in range(max_pages):
        ck = _cache_key("markets", str(offset), str(page_size))
        cached = read_cache(_SUBDIR, ck, max_age_hours=5/60)  # 5 min TTL
        if cached is not None:
            data = cached
        else:
            url = f"{POLYMARKET_GAMMA_BASE}/markets"
            params = {
                "limit": page_size,
                "offset": offset,
                "active": "true",
                "closed": "false",
            }
            data = await _get_json(url, params)
            if data:
                write_cache(_SUBDIR, ck, data)

        if not data:
            break

        for item in data:
            if _is_weather_market(item):
                parsed = _raw_to_parsed_market(item)
                if parsed is not None and parsed.condition_id not in seen_ids:
                    market_markets.append(parsed)
                    seen_ids.add(parsed.condition_id)

        if len(data) < page_size:
            break
        offset += page_size

    log.info("Fetched %d additional markets from /markets endpoint", len(market_markets))
    all_markets = event_markets + market_markets

    # Enrich any markets where Gamma returned empty clobTokenIds — without
    # real token IDs the WebSocket subscription is silent and all prices
    # will be zero.
    await _enrich_missing_token_ids(all_markets)

    return all_markets


async def fetch_weather_events(
    *,
    max_pages: int = 50,
    page_size: int = 100,
) -> list[ParsedMarket]:
    """
    Fetch active daily weather events from Gamma ``GET /events``.

    Daily weather markets on Polymarket are structured as events with
    child markets (one per temperature bin).  This function fetches the
    parent events, filters for daily weather markets, and parses them
    using :func:`parse_daily_weather_event`.
    """
    markets: list[ParsedMarket] = []
    offset = 0

    for _ in range(max_pages):
        ck = _cache_key("events", str(offset), str(page_size))
        cached = read_cache(_SUBDIR, ck, max_age_hours=5/60)  # 5 min TTL
        if cached is not None:
            data = cached
        else:
            url = f"{POLYMARKET_GAMMA_BASE}/events"
            params = {
                "limit": page_size,
                "offset": offset,
                "active": "true",
                "closed": "false",
            }

            try:
                data = await _get_json(url, params)
                if data:
                    write_cache(_SUBDIR, ck, data)
            except Exception as e:
                log.warning("Gamma /events fetch failed (offset=%d): %s", offset, e)
                break

        if not data:
            break

        for event in data:
            title = event.get("title", "").lower()
            # Filter for daily weather events
            if "highest temperature in" not in title and "temperature" not in title:
                continue

            parsed = parse_daily_weather_event(event)
            if parsed is not None:
                markets.append(parsed)

        if len(data) < page_size:
            break
        offset += page_size

    log.info("Parsed %d daily weather events from /events", len(markets))
    return markets


def _is_weather_market(item: dict) -> bool:
    """Return True if the market looks like a weather market.

    Rejects markets that match non-weather blocklist terms (politics,
    crypto, sports, etc.) even if a weather keyword happens to appear.
    """
    searchable = " ".join([
        item.get("question", ""),
        item.get("description", ""),
        " ".join(item.get("tags", []) if isinstance(item.get("tags"), list) else []),
    ]).lower()

    # Reject non-weather markets first
    if any(bl in searchable for bl in NON_WEATHER_BLOCKLIST):
        return False

    # Check for keywords
    for kw in WEATHER_KEYWORDS + CLIMATE_KEYWORDS:
        if kw in searchable:
            # For short or common words, require word boundaries to avoid
            # false positives like "ukraine" matching "rain" or "wind"
            # in names.
            if kw in ("rain", "temp", "wind", "ice", "cold", "heat"):
                if not re.search(rf"\b{kw}\b", searchable):
                    continue
            return True

    return False


def _extract_condition_id(item: dict) -> str:
    """
    FIX #9: Consistently extract a condition/market ID from a raw API dict.

    Polymarket's Gamma API uses ``conditionId`` for CLOB-linked markets and
    ``id`` as a fallback UUID.  Always prefer ``conditionId`` when present and
    non-empty, as it is the canonical identifier used by the CLOB and PMXT.
    """
    cid = item.get("conditionId", "").strip()
    if cid:
        return cid
    return item.get("id", "").strip()


def _raw_to_parsed_market(item: dict) -> ParsedMarket | None:
    """Convert a raw Gamma API market dict to our ParsedMarket dataclass."""
    try:
        import json
        outcomes = item.get("outcomes", "")
        if isinstance(outcomes, str):
            if outcomes.strip().startswith("["):
                try:
                    outcomes = json.loads(outcomes)
                except json.JSONDecodeError:
                    outcomes = [o.strip() for o in outcomes.split(",") if o.strip()]
            else:
                outcomes = [o.strip() for o in outcomes.split(",") if o.strip()]

        outcome_prices_raw = item.get("outcomePrices", "")
        if isinstance(outcome_prices_raw, str):
            if outcome_prices_raw.strip().startswith("["):
                try:
                    outcome_prices_raw = json.loads(outcome_prices_raw)
                    outcome_prices = [float(p) for p in outcome_prices_raw]
                except json.JSONDecodeError:
                    outcome_prices = [float(p.strip()) for p in outcome_prices_raw.split(",") if p.strip()]
            else:
                outcome_prices = [float(p.strip()) for p in outcome_prices_raw.split(",") if p.strip()]
        elif isinstance(outcome_prices_raw, list):
            outcome_prices = [float(p) for p in outcome_prices_raw]
        else:
            outcome_prices = []

        # Token IDs may come from clobTokenIds
        token_ids_raw = item.get("clobTokenIds", "")
        if isinstance(token_ids_raw, str):
            if token_ids_raw.strip().startswith("["):
                try:
                    token_ids = json.loads(token_ids_raw)
                    token_ids = [str(t) for t in token_ids]
                except json.JSONDecodeError:
                    token_ids = [t.strip() for t in token_ids_raw.split(",") if t.strip()]
            else:
                token_ids = [t.strip() for t in token_ids_raw.split(",") if t.strip()]
        elif isinstance(token_ids_raw, list):
            token_ids = [str(t) for t in token_ids_raw]
        else:
            token_ids = []

        description = item.get("description", "")
        question = item.get("question", "")

        # FIX #9: use unified condition ID extractor
        market = ParsedMarket(
            condition_id=_extract_condition_id(item),
            question=question,
            outcomes=outcomes,
            outcome_prices=outcome_prices,
            token_ids=token_ids,
            volume=float(item.get("volume", 0) or 0),
            end_date=item.get("endDate", ""),
            description=description,
        )

        # FIX #10: detect unit once here; pass it into parse_resolution_rules
        group_title = item.get("groupItemTitle", "")
        detected_unit = _detect_unit(question + " " + description + " " + group_title)

        # Attempt to parse resolution rules from the description and title
        parsed = parse_resolution_rules(
            description, question, group_title, outcomes, detected_unit=detected_unit
        )
        market.station_id = parsed.get("station_id")
        market.metric = parsed.get("metric")
        market.bins = parsed.get("bins", [])
        market.timezone = parsed.get("timezone")
        market.resolution_date = parsed.get("resolution_date")

        # --- City-name fallback for station matching ---
        if market.station_id is None:
            from weatheredge.stations import CITY_TO_STATION
            city = _extract_city_from_title(question)
            if city:
                for k, v in CITY_TO_STATION.items():
                    if k.lower() == city.lower():
                        market.station_id = v
                        break

        # --- Unit Normalization ---
        # Everything normalised to Celsius for probability.py.
        # FIX #10: reuse the already-detected unit rather than detecting again.
        from weatheredge.stations import get_station
        station = get_station(market.station_id) if market.station_id else None
        market_unit = station.preferred_unit if (station and station.preferred_unit) else detected_unit

        if market_unit == "F" and market.bins:
            market.bins = [
                (
                    fahrenheit_to_celsius(low) if low != float("-inf") else float("-inf"),
                    fahrenheit_to_celsius(high) if high != float("inf") else float("inf"),
                )
                for low, high in market.bins
            ]

        return market
    except Exception:
        # FIX #4: log the exception instead of silently swallowing it
        log.exception(
            "Failed to parse market (conditionId=%r, question=%r)",
            item.get("conditionId", item.get("id", "unknown")),
            item.get("question", ""),
        )
        return None


# =========================================================================
# Resolution rule parsing
# =========================================================================

# Common station ID patterns found in Polymarket weather descriptions
_STATION_PATTERN = re.compile(
    r"\b([A-Z]{4})\b"  # ICAO 4-letter codes (NZWN, EGLL, KORD, etc.)
)

# Wunderground station references
_WU_STATION_PATTERN = re.compile(
    r"(?:Weather\s+Underground|Wunderground|WU|weather\.com).*?(?:station\s+)?([A-Z]{2,5}[0-9]*[A-Z]*)",
    re.IGNORECASE,
)

# Date patterns
_DATE_PATTERN = re.compile(
    r"(?:on|for|by)\s+(\w+\s+\d{1,2}(?:,?\s*\d{4})?)",
    re.IGNORECASE,
)

# Metric detection
_METRIC_PATTERNS = {
    "tmax": re.compile(r"\b(?:high\s+temp|maximum\s+temp|daily\s+high|high\b)", re.IGNORECASE),
    "tmin": re.compile(r"\b(?:low\s+temp|minimum\s+temp|daily\s+low|overnight\s+low)", re.IGNORECASE),
    "precip": re.compile(r"\b(?:precip|rainfall|rain\s+total|precipitation)", re.IGNORECASE),
}

# Temperature bin patterns — e.g., "Under 18", "18-20", "20-22", "Over 24"
_BIN_UNDER = re.compile(r"[Uu]nder\s+([\d.]+)")
_BIN_OVER = re.compile(r"[Oo]ver\s+([\d.]+)")
# FIX #7: require °/degree context around the range to avoid matching
# version strings, coordinates, and other non-temperature numeric ranges.
_BIN_RANGE = re.compile(
    r"([\d.]+)\s*[-–]\s*([\d.]+)\s*(?:°|degrees?\b)",
    re.IGNORECASE,
)

# Known ICAO codes for our priority stations — used to prioritise matches
_KNOWN_ICAO = {"NZWN", "EGLL", "KJFK", "KNYC", "KORD", "YSSY", "YMML", "RJTT"}


def parse_resolution_rules(
    description: str,
    question: str = "",
    group_title: str = "",
    outcomes: list[str] | None = None,
    *,
    detected_unit: str = "C",  # FIX #10: accept pre-detected unit to avoid double-detection
) -> dict[str, Any]:
    """
    Extract station ID, metric, bins, timezone, and date from a market's
    description and question text.

    Returns a dict with keys: station_id, metric, bins, timezone, resolution_date.
    Missing fields are None.
    """
    if outcomes is None:
        outcomes = []

    full_text = f"{question} {description} {group_title}"
    result: dict[str, Any] = {
        "station_id": None,
        "metric": None,
        "bins": [],
        "timezone": None,
        "resolution_date": None,
    }

    # --- Global Climate Anomaly Check ---
    is_climate = any(kw in full_text.lower() for kw in CLIMATE_KEYWORDS)
    if is_climate:
        result["station_id"] = "GLOBAL"
        result["metric"] = "tmax"  # Use tmax as a proxy for anomaly

        # Determine the target bin from groupItemTitle or question
        search_text = f"{group_title} {question}"
        target_bin = None

        under = _BIN_UNDER.search(search_text)
        if under:
            target_bin = (float("-inf"), float(under.group(1)))
        elif "<" in search_text:
            m = re.search(r"<([\d.]+)", search_text)
            if m:
                target_bin = (float("-inf"), float(m.group(1)))

        over = _BIN_OVER.search(search_text)
        if over:
            target_bin = (float(over.group(1)), float("inf"))
        elif ">" in search_text:
            m = re.search(r">([\d.]+)", search_text)
            if m:
                target_bin = (float(m.group(1)), float("inf"))

        rng = _BIN_RANGE.search(search_text)
        if rng:
            target_bin = (float(rng.group(1)), float(rng.group(2)))

        if target_bin and len(outcomes) == 2 and set(o.lower() for o in outcomes) == {"yes", "no"}:
            # Standardise Yes/No outcomes to represent the target bin.
            # Index 0 is usually "Yes", Index 1 is "No".
            if outcomes[0].lower() == "yes":
                result["bins"] = [target_bin, (float("-inf"), float("inf"))]
            else:
                result["bins"] = [(float("-inf"), float("inf")), target_bin]

    # --- Station ID ---
    if result["station_id"] is None:
        # Try Wunderground reference first
        wu_match = _WU_STATION_PATTERN.search(full_text)
        if wu_match:
            result["station_id"] = wu_match.group(1).upper()
        else:
            # Fall back to ICAO codes, preferring known stations
            icao_matches = _STATION_PATTERN.findall(full_text)
            for code in icao_matches:
                if code in _KNOWN_ICAO:
                    result["station_id"] = code
                    break
            if result["station_id"] is None and icao_matches:
                # Take first plausible ICAO code
                result["station_id"] = icao_matches[0]

    # --- Metric ---
    if result["metric"] is None:
        for metric_name, pattern in _METRIC_PATTERNS.items():
            if pattern.search(full_text):
                result["metric"] = metric_name
                break
        if result["metric"] is None:
            # Default to tmax for temperature markets
            if re.search(r"\btemp", full_text, re.IGNORECASE):
                result["metric"] = "tmax"

    # --- Bins ---
    # FIX #8: for regular (non-climate) markets, parse bins from outcomes when
    # available, rather than always returning an empty list.
    if not result["bins"] and outcomes:
        parsed_bins = parse_bins_from_outcomes(outcomes)
        if parsed_bins:
            # Convert to Celsius if the market unit is Fahrenheit
            if detected_unit == "F":
                parsed_bins = [
                    (
                        fahrenheit_to_celsius(lo) if lo != float("-inf") else float("-inf"),
                        fahrenheit_to_celsius(hi) if hi != float("inf") else float("inf"),
                    )
                    for lo, hi in parsed_bins
                ]
            result["bins"] = parsed_bins

    # --- Timezone ---
    tz_match = re.search(
        r"\b(UTC|EST|CST|MST|PST|NZST|NZDT|AEST|AEDT|GMT|BST|JST|ET|CT|PT)\b",
        full_text,
    )
    if tz_match:
        result["timezone"] = tz_match.group(1)

    # --- Resolution date ---
    date_match = _DATE_PATTERN.search(question)
    if date_match:
        try:
            raw = date_match.group(1).strip().rstrip(",")
            # Try common formats
            for fmt in ("%B %d %Y", "%B %d, %Y", "%B %d", "%b %d %Y", "%b %d"):
                try:
                    parsed_date = datetime.strptime(raw, fmt).date()
                    # If year is missing, assume current/next year
                    if parsed_date.year == 1900:
                        today = date.today()
                        parsed_date = parsed_date.replace(year=today.year)
                        if parsed_date < today:
                            parsed_date = parsed_date.replace(year=today.year + 1)
                    result["resolution_date"] = parsed_date
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    return result


def parse_bins_from_outcomes(outcomes: list[str]) -> list[tuple[float, float]]:
    """
    Convert market outcome labels like ["Under 18", "18-20", "20-22", "Over 24"]
    into bin tuples: [(-inf, 18), (18, 20), (20, 22), (24, inf)].
    """
    bins: list[tuple[float, float]] = []

    for label in outcomes:
        label = label.strip()

        under = _BIN_UNDER.search(label)
        if under:
            bins.append((float("-inf"), float(under.group(1))))
            continue

        over = _BIN_OVER.search(label)
        if over:
            bins.append((float(over.group(1)), float("inf")))
            continue

        rng = _BIN_RANGE.search(label)
        if rng:
            bins.append((float(rng.group(1)), float(rng.group(2))))
            continue

    return bins


# =========================================================================
# Price fetching
# =========================================================================

async def fetch_midpoint_prices(token_ids: list[str]) -> dict[str, float]:
    """
    Fetch midpoint prices for the given Polymarket CLOB token IDs.
    Returns {token_id: midpoint_price}.
    """
    if not token_ids:
        return {}

    sorted_ids = sorted(token_ids)
    ck = _cache_key("midpoints", *sorted_ids)
    cached = read_cache(_SUBDIR, ck, max_age_hours=2/60)  # 2 min TTL
    if cached is not None:
        return cached

    url = f"{POLYMARKET_CLOB_BASE}/midpoints"
    params = {"token_ids": ",".join(token_ids)}

    try:
        data = await _get_json(url, params)
        result = {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
        if result:
            write_cache(_SUBDIR, ck, result)
        return result
    except Exception:
        return {}


# =========================================================================
# Daily weather event parsing
# =========================================================================

def fahrenheit_to_celsius(f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (f - 32.0) * 5.0 / 9.0


def _detect_unit(text: str) -> str:
    """Detect if Fahrenheit or Celsius from text."""
    search = text.lower()

    # Check for explicit phrases first
    if "degrees celsius" in search or "degrees c" in search:
        return "C"
    if "degrees fahrenheit" in search or "degrees f" in search:
        return "F"

    # Check for symbols with numbers
    if re.search(r"\d+\s*°c", search) or re.search(r"\bcelsius\b", search):
        return "C"
    if re.search(r"\d+\s*°f", search) or re.search(r"\bfahrenheit\b", search):
        return "F"

    # If both or neither, check for symbols
    if "°c" in search:
        return "C"
    if "°f" in search:
        return "F"

    # Default to Celsius for international safety,
    # but the calling code should prioritize station metadata.
    return "C"


def _extract_city_from_title(title: str) -> str | None:
    """
    Extract city name from titles like:
        'Highest temperature in NYC on March 3?'
        'Will the high temperature in New York City reach 80°F?'

    FIX #5: broadened regex so multi-word city names and varied phrasing
    are handled correctly.  The previous pattern broke on cities containing
    prepositions (e.g. "Port of Spain") and on titles that used "reach" or
    "be" but not immediately after the city name.
    """
    # Primary pattern: "in <city> on/be/reach/stay"
    m = re.search(
        r"\bin\s+([A-Za-z][A-Za-z\s\-\.]{1,40}?)\s+(?:on|be|reach|stay)\b",
        title,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    # Fallback: "in <city>?" at end of title (no action verb present)
    m = re.search(
        r"\bin\s+([A-Za-z][A-Za-z\s\-\.]{1,40}?)\s*\??$",
        title.strip(),
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    return None


def _extract_date_from_title(title: str) -> date | None:
    """Extract resolution date from 'Highest temperature in NYC on March 3?'"""
    match = re.search(
        r"\bon\s+(\w+)\s+(\d{1,2})\??$",
        title.strip(),
        re.IGNORECASE,
    )
    if not match:
        return None

    month_str = match.group(1)
    day_str = match.group(2)

    for fmt in ("%B %d", "%b %d"):
        try:
            parsed = datetime.strptime(f"{month_str} {day_str}", fmt)
            # Year is missing — use current year, adjust later from endDate
            return parsed.replace(year=date.today().year).date()
        except ValueError:
            continue
    return None


def parse_daily_weather_event(event: dict) -> ParsedMarket | None:
    """
    Parse a full Polymarket daily weather event (with child markets) into
    a single ParsedMarket with complete bins and outcome prices.

    Each child market is a single Yes/No binary for one temperature bin.
    We reconstruct the full multi-bin market from all children.

    The ``description`` field of the returned market is appended with a
    ``CHILD_IDS:`` footer so that downstream PMXT enrichers can recover
    the per-bin condition IDs without an extra API round-trip.
    """
    import json
    from weatheredge.stations import CITY_TO_STATION, get_station

    title = event.get("title", "")
    markets = event.get("markets", [])
    description = markets[0].get("description", "") if markets else ""

    if not markets:
        return None

    # FIX #10: detect unit once and pass it through everywhere
    unit = _detect_unit(title + " " + description)

    # --- Extract city and resolve to station ---
    city = _extract_city_from_title(title)
    if not city:
        log.warning("Could not extract city from title: %s", title)
        return None

    station_id = None
    for k, v in CITY_TO_STATION.items():
        if k.lower() == city.lower():
            station_id = v
            break

    if not station_id:
        log.warning("No station mapping found for city: %s", city)
        return None

    log.info("Market: %s -> Station: %s, Unit: %s", title, station_id, unit)

    # --- Extract resolution date ---
    resolution_date = None
    child_end = markets[0].get("endDate", "") if markets else ""
    end_date_str = event.get("endDate", "")
    if child_end:
        try:
            resolution_date = datetime.fromisoformat(
                child_end.replace("Z", "+00:00")
            ).date()
        except (ValueError, TypeError):
            resolution_date = _extract_date_from_title(title)
    else:
        resolution_date = _extract_date_from_title(title)

    if end_date_str:
        try:
            resolution_date = datetime.fromisoformat(
                end_date_str.replace("Z", "+00:00")
            ).date()
        except (ValueError, TypeError):
            pass

    if resolution_date is None:
        resolution_date = _extract_date_from_title(title)

    # --- Build bins from child markets ---
    # Sort by groupItemThreshold to preserve natural bin order
    sorted_markets = sorted(
        markets,
        key=lambda m: int(m.get("groupItemThreshold", "0")),
    )

    bins: list[tuple[float, float]] = []
    outcome_prices: list[float] = []
    outcomes: list[str] = []
    child_condition_ids: list[str] = []
    child_token_ids: list[str] = []
    # FIX #3: track winning bin and log it for diagnostics
    winning_bin: int | None = None

    for i, m in enumerate(sorted_markets):
        group_title = m.get("groupItemTitle", "")
        outcomes.append(group_title)

        bin_tuple = _parse_bin_from_group_title(group_title)
        if bin_tuple is None:
            continue

        # FIX #10: use already-detected unit
        if unit == "F":
            low = fahrenheit_to_celsius(bin_tuple[0]) if bin_tuple[0] != float("-inf") else float("-inf")
            high = fahrenheit_to_celsius(bin_tuple[1]) if bin_tuple[1] != float("inf") else float("inf")
            bin_tuple = (low, high)

        bins.append(bin_tuple)

        # FIX #9: use unified condition ID extractor for child markets
        child_cid = _extract_condition_id(m)
        child_condition_ids.append(child_cid)

        clob_raw = m.get("clobTokenIds", "")
        if isinstance(clob_raw, str):
            clob_parts = [t.strip() for t in clob_raw.split(",") if t.strip()]
        elif isinstance(clob_raw, list):
            clob_parts = [str(t) for t in clob_raw]
        else:
            clob_parts = []
        child_token_ids.append(clob_parts[0] if clob_parts else child_cid)

        prices_raw = m.get("outcomePrices", "")
        if isinstance(prices_raw, str) and prices_raw.strip().startswith("["):
            try:
                prices_list = json.loads(prices_raw)
                yes_price = float(prices_list[0])
            except (json.JSONDecodeError, IndexError, ValueError):
                yes_price = 0.0
        elif isinstance(prices_raw, list):
            yes_price = float(prices_raw[0]) if prices_raw else 0.0
        else:
            yes_price = 0.0

        outcome_prices.append(yes_price)

        # FIX #3: record winning bin and log it
        if yes_price >= 0.8:
            winning_bin = i

    if not bins:
        return None

    if winning_bin is not None:
        log.info(
            "Market '%s' appears resolved: winning bin index=%d (%s)",
            title,
            winning_bin,
            outcomes[winning_bin] if winning_bin < len(outcomes) else "?",
        )

    # FIX #2: append child condition IDs to description so PMXT enrichers
    # can recover them without an extra API round-trip.
    child_ids_footer = "\nCHILD_IDS:" + ",".join(child_condition_ids) if child_condition_ids else ""
    full_description = description + child_ids_footer

    market = ParsedMarket(
        condition_id=event.get("id", ""),
        question=title,
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        token_ids=child_token_ids,
        volume=float(event.get("volume", 0) or 0),
        end_date=end_date_str,
        description=full_description,
    )

    market.station_id = station_id
    market.metric = "tmax"
    market.bins = bins
    market.timezone = None
    market.resolution_date = resolution_date

    return market


def _parse_bin_from_group_title(title: str) -> tuple[float, float] | None:
    """
    Parse a temperature bin from a Polymarket groupItemTitle.

    Examples:
        "42-43°F"           -> (42, 43)
        "44°F or higher"    -> (44, inf)
        "33°F or below"     -> (-inf, 33)
        "14°C"              -> (14, 15)   FIX #6: 1-degree-wide bin, not zero-width
        "-5°C"              -> (-5, -4)
    """
    title = title.strip()

    # "X or higher" / "X°F or higher"
    m = re.match(r"(-?[\d.]+)\s*°?[FCfc]?\s+or\s+higher", title, re.IGNORECASE)
    if m:
        return (float(m.group(1)), float("inf"))

    # "X or below" / "X°F or below"
    m = re.match(r"(-?[\d.]+)\s*°?[FCfc]?\s+or\s+below", title, re.IGNORECASE)
    if m:
        return (float("-inf"), float(m.group(1)))

    # "X-Y" range (with optional °F/°C)
    m = re.match(r"(-?[\d.]+)\s*[-–]\s*(-?[\d.]+)\s*°?[FCfc]?", title)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    # "<X" / "< X"
    m = re.match(r"<\s*(-?[\d.]+)", title)
    if m:
        return (float("-inf"), float(m.group(1)))

    # ">X" / "> X"
    m = re.match(r">\s*(-?[\d.]+)", title)
    if m:
        return (float(m.group(1)), float("inf"))

    # FIX #6: Single degree "14°C" or "-5°C" — use a 1-degree-wide bin
    # so the interval is non-degenerate and will match real temperature values.
    m = re.match(r"(-?[\d.]+)\s*°[FCfc]?$", title)
    if m:
        lo = float(m.group(1))
        return (lo, lo + 1.0)

    return None