import httpx
import json

def fetch_weather_by_tag():
    print("Searching via series/tags...")
    # Daily weather markets may be under specific series or tags
    resp = httpx.get("https://gamma-api.polymarket.com/events?slug=highest-temperature-in-nyc-on-march-3")
    
    if resp.status_code == 200:
        events = resp.json()
        print(f"Events found by slug: {len(events)}")
        if events:
            e = events[0]
            print(json.dumps(e, indent=2))
    else:
        print(f"Failed slug lookup: {resp.status_code}")

fetch_weather_by_tag()
