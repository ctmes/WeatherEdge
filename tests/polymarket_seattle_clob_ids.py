#!/usr/bin/env python3
import httpx
import json
from typing import List, Optional

GAMMA_BASE = "https://gamma-api.polymarket.com"


def find_event_by_title_substring(title_substring: str) -> Optional[dict]:
    """
    Scan active, open events and return the first event whose title
    contains the given substring (case-insensitive).
    """
    offset = 0
    limit = 100
    title_substring = title_substring.lower()

    with httpx.Client(timeout=10.0, verify=False) as client:
        while True:
            r = client.get(
                f"{GAMMA_BASE}/events",
                params={
                    "limit": limit,
                    "offset": offset,
                    "active": "true",
                    "closed": "false",
                },
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break

            for event in data:
                title = (event.get("title") or "").lower()
                if title_substring in title:
                    return event

            if len(data) < limit:
                break
            offset += limit

    return None


def get_markets_for_event(event_id: str) -> List[dict]:
    """
    Fetch all markets for a given event id and return the raw market dicts.
    Markets, not events, carry clobTokenIds on the Gamma API.[web:20][web:26][web:31]
    """
    markets: List[dict] = []

    with httpx.Client(timeout=10.0, verify=False) as client:
        offset = 0
        limit = 100

        while True:
            r = client.get(
                f"{GAMMA_BASE}/markets",
                params={
                    "eventId": event_id,
                    "limit": limit,
                    "offset": offset,
                    "active": "true",
                    "closed": "false",
                },
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break

            markets.extend(batch)

            if len(batch) < limit:
                break
            offset += limit

    return markets


def extract_clob_token_ids_from_markets(markets: List[dict]) -> List[str]:
    """
    Extract and normalize clobTokenIds from a list of markets.
    clobTokenIds is a JSON-stringified list in the Gamma response.[web:20]
    """
    all_ids: List[str] = []

    for m in markets:
        raw = m.get("clobTokenIds")
        if not raw:
            continue
        try:
            ids = json.loads(raw)
            if isinstance(ids, list):
                all_ids.extend(str(x) for x in ids)
        except Exception as e:
            print(f"Failed to parse clobTokenIds for market {m.get('id')}: {e}")

    return all_ids


def main() -> None:
    # You can tweak this to another exact title or substring
    search_term = "highest temperature in seattle on february 25"

    print(f"Searching for event with title containing: {search_term!r}")
    event = find_event_by_title_substring(search_term)
    if not event:
        print("No matching event found.")
        return

    print(f"Found event:")
    print(json.dumps(event, indent=2))

    event_id = event["id"]
    print(f"\nFetching markets for event id: {event_id}")
    markets = get_markets_for_event(event_id)
    print(f"Found {len(markets)} markets for this event.")

    # Dump markets for debugging
    for m in markets:
        print("\nMarket:")
        print(json.dumps(m, indent=2))

    clob_ids = extract_clob_token_ids_from_markets(markets)
    print("\nExtracted CLOB token ids:")
    for cid in clob_ids:
        print(cid)

    # Optionally, write mapping to disk
    out = {
        "event": event,
        "markets": markets,
        "clobTokenIds": clob_ids,
    }
    with open("seattle_feb25_clob_ids.json", "w") as f:
        json.dump(out, f, indent=2)
    print('\nWrote seattle_feb25_clob_ids.json')


if __name__ == "__main__":
    main()