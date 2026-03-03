import requests
import json

def diagnose():
    url = "https://gamma-api.polymarket.com/markets"
    params = {"limit": 100, "closed": "true", "active": "false"}
    print(f"Fetching {url} with params {params}...")
    
    try:
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        print(f"Fetched {len(data)} markets.")
        
        weather_keywords = ["temperature", "temp", "high", "low", "weather", "rain", "precipitation", "rainfall"]
        
        found = 0
        for item in data:
            q = item.get("question", "").lower()
            d = item.get("description", "").lower()
            tags = " ".join([t.lower() for t in item.get("tags", []) if isinstance(t, str)])
            searchable = f"{q} {d} {tags}"
            
            matches = [kw for kw in weather_keywords if kw in searchable]
            if matches:
                found += 1
                end_date = item.get("endDate")
                print(f"MATCH: {q[:60]}... | Date: {end_date} | KWs: {matches}")
        
        print(f"\nFound {found} potential weather markets in top 100.")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    diagnose()
