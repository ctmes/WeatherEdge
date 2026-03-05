import httpx
import json

def fetch_gamma_ids():
    url = "https://gamma-api.polymarket.com/events?limit=5&active=true"
    print(f"Fetching {url}...")
    r = httpx.get(url)
    data = r.json()
    for e in data:
        print(f"Event: {e['title']}")
        for m in e.get("markets", []):
            print(f"  Market: {m.get('groupItemTitle')}")
            print(f"    Condition ID: {m.get('conditionId')}")
            print(f"    Token IDs: {m.get('clobTokenIds')}")

if __name__ == "__main__":
    fetch_gamma_ids()
