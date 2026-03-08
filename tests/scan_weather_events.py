#!/usr/bin/env python3
"""Scan Polymarket events for anything weather-related."""
import httpx
import json

GAMMA_BASE = "https://gamma-api.polymarket.com"
KEYWORDS = ["temperature", "weather", "seattle", "celsius", "fahrenheit",
            "highest temp", "lowest temp", "tmax", "tmin", "degrees"]


def main():
    offset = 0
    batch_size = 100
    max_pages = 60
    total = 0
    weather_hits = []

    with httpx.Client(verify=False, timeout=30) as client:
        for _ in range(max_pages):
            r = client.get(f"{GAMMA_BASE}/events",
                           params={"limit": batch_size, "offset": offset})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break

            total += len(batch)
            for e in batch:
                title = (e.get("title") or "").lower()
                if any(kw in title for kw in KEYWORDS):
                    weather_hits.append({
                        "title": e.get("title"),
                        "id": e.get("id"),
                        "active": e.get("active"),
                        "closed": e.get("closed"),
                        "endDate": e.get("endDate"),
                        "slug": e.get("slug"),
                    })

            if len(batch) < batch_size:
                break
            offset += batch_size

    print(f"Scanned {total} events")
    print(f"Weather-related hits: {len(weather_hits)}")
    print()
    for h in weather_hits[:30]:
        print(f"  [{h['active']}/{h['closed']}] {h['title']}")
        print(f"    ends: {h['endDate']}  id: {h['id']}")

    with open("weather_events_scan.json", "w") as f:
        json.dump(weather_hits, f, indent=2)
    print(f"\nSaved {len(weather_hits)} results to weather_events_scan.json")


if __name__ == "__main__":
    main()