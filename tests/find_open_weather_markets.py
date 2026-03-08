#!/usr/bin/env python3
"""Find currently open weather markets on Polymarket."""
import httpx
import json

GAMMA_BASE = "https://gamma-api.polymarket.com"


def main():
    with httpx.Client(verify=False, timeout=30) as client:

        # ── Strategy 1: fetch by series slug directly ──────────────────
        print("=== Strategy 1: Series slugs ===")
        series_slugs = [
            "seattle-daily-weather",
            "new-york-daily-weather",
            "los-angeles-daily-weather",
            "chicago-daily-weather",
            "miami-daily-weather",
            "daily-weather",
            "temperature-daily",
        ]
        for slug in series_slugs:
            r = client.get(f"{GAMMA_BASE}/events", params={"seriesSlug": slug, "limit": 10})
            events = r.json()
            if events:
                for e in events:
                    print(f"  [{slug}] {e.get('title')} | active={e.get('active')} closed={e.get('closed')} end={e.get('endDate')}")

        # ── Strategy 2: /markets endpoint keyword search ───────────────
        print("\n=== Strategy 2: /markets keyword search ===")
        keywords = ["highest temperature", "lowest temperature", "temperature in"]
        for kw in keywords:
            r = client.get(f"{GAMMA_BASE}/markets", params={
                "limit": 20,
                "active": "true",
                "closed": "false",
                "keyword": kw,
            })
            markets = r.json()
            if isinstance(markets, list) and markets:
                print(f"  keyword='{kw}': {len(markets)} results")
                for m in markets[:5]:
                    print(f"    {m.get('question','')[:80]}")
                    print(f"    accepting={m.get('acceptingOrders')} clob={m.get('clobTokenIds','')[:60]}")

        # ── Strategy 3: tag-based search ───────────────────────────────
        print("\n=== Strategy 3: tag search ===")
        for tag in ["temperature", "weather", "Daily Temperature"]:
            r = client.get(f"{GAMMA_BASE}/events", params={
                "limit": 20,
                "active": "true",
                "closed": "false",
                "tag": tag,
            })
            events = r.json()
            if isinstance(events, list) and events:
                print(f"  tag='{tag}': {len(events)} events")
                for e in events[:5]:
                    print(f"    {e.get('title')} | end={e.get('endDate')}")

        # ── Strategy 4: tag_slug search ────────────────────────────────
        print("\n=== Strategy 4: tag_slug search ===")
        for slug in ["weather", "temperature", "daily-temperature"]:
            r = client.get(f"{GAMMA_BASE}/events", params={
                "limit": 20,
                "active": "true",
                "tag_slug": slug,
            })
            events = r.json()
            if isinstance(events, list) and events:
                print(f"  tag_slug='{slug}': {len(events)} events")
                for e in events[:5]:
                    print(f"    {e.get('title')} | end={e.get('endDate')}")

        # ── Strategy 5: fetch series 10734 directly ────────────────────
        print("\n=== Strategy 5: series id 10734 (Seattle Daily Weather) ===")
        r = client.get(f"{GAMMA_BASE}/series/10734")
        if r.status_code == 200:
            print(json.dumps(r.json(), indent=2)[:1000])
        else:
            print(f"  status {r.status_code}")

        r2 = client.get(f"{GAMMA_BASE}/events", params={"seriesId": "10734", "limit": 10})
        events = r2.json()
        if isinstance(events, list):
            print(f"  seriesId=10734 events: {len(events)}")
            for e in events:
                print(f"    {e.get('title')} | active={e.get('active')} end={e.get('endDate')}")


if __name__ == "__main__":
    main()