#!/usr/bin/env python3
import httpx
import json
from typing import List, Dict

GAMMA_BASE = "https://gamma-api.polymarket.com"
TITLE_SUBSTRING = "highest temperature in".lower()


def fetch_all_events_paginated(batch_size: int = 100, max_pages: int = 60) -> List[dict]:
    """Paginate /events — weather markets appear deep in the list (~offset 3000-5000)."""
    events = []
    offset = 0

    timeout = httpx.Timeout(30.0, connect=60.0)
    
    with httpx.Client(timeout=timeout, verify=False) as client:
        while offset // batch_size < max_pages:
            params = {
                "limit": batch_size,
                "offset": offset,
                # Don't filter active/closed at event level — weather events 
                # show active=true even when some child markets are closed
            }
            
            r = client.get(f"{GAMMA_BASE}/events", params=params)
            r.raise_for_status()
            batch = r.json()
            
            if not batch:
                break
                
            events.extend(batch)
            
            if len(batch) < batch_size:
                break
            offset += batch_size

    return events


def fetch_markets_for_event(event_id: str) -> List[Dict]:
    params = {"eventId": event_id, "active": "true", "closed": "false", "limit": 100}
    timeout = httpx.Timeout(20.0, connect=60.0)
    
    with httpx.Client(timeout=timeout, verify=False) as client:
        r = client.get(f"{GAMMA_BASE}/markets", params=params)
        r.raise_for_status()
        return r.json()


def parse_clob_token_ids(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        return [str(x) for x in json.loads(raw)]
    except:
        return []


def main() -> None:
    print("🔍 Scanning ALL active events for 'Highest temperature in'...")
    all_events = fetch_all_events_paginated()
    print(f"Total events scanned: {len(all_events)}")
    
    temp_events = [ev for ev in all_events if TITLE_SUBSTRING in (ev.get("title") or "").lower()]
    print(f"✅ Found {len(temp_events)} matching events!\n")

    results = []
    for ev in temp_events:
        title = ev["title"]
        event_id = ev["id"]
        slug = ev.get("slug")
        url = f"https://polymarket.com/event/{slug}" if slug else "N/A"

        print("=" * 80)
        print(title)
        print(f"Event ID: {event_id}")
        print(f"URL: {url}")

        markets = fetch_markets_for_event(event_id)
        print(f"📊 Markets: {len(markets)}")

        event_result = {"event": ev, "markets": []}
        for m in markets:
            m_title = m.get("question") or m.get("title") or ""
            clob_ids = parse_clob_token_ids(m.get("clobTokenIds"))
            
            print(f"  📈 {m_title[:60]}...")
            print(f"     CLOB IDs: {clob_ids[:2]}{'...' if len(clob_ids) > 2 else ''}")
            print(f"     YES: {m.get('prices', {}).get('yes')}")

            event_result["markets"].append({
                "market": m,
                "clobTokenIds": clob_ids
            })

        results.append(event_result)
        print()

    with open("highest_temp_full.json", "w") as f:
        json.dump(results, f, indent=2)
    print("✅ Saved highest_temp_full.json")


if __name__ == "__main__":
    main()
