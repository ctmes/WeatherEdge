import httpx
import json

def fetch_historical_daily_weather():
    limit = 500
    offset = 0
    matched = 0
    
    # Tag '84' is "Weather"
    # Tag '101757' is "Recurring"
    # The title string is "highest temperature in"
    
    # Only closed/resolved markets to verify backtesting history
    
    print("Searching for resolved daily weather markets...")
    while True:
        url = f"https://gamma-api.polymarket.com/events?closed=true&limit={limit}&offset={offset}"
        resp = httpx.get(url)
        if resp.status_code != 200:
            break
            
        events = resp.json()
        if not events:
            break
            
        for e in events:
            title = e.get('title', '').lower()
            if 'highest temperature in' in title:
                print(f"[{e.get('endDate')[:10]}] {e.get('title')}")
                matched += 1
                
        offset += limit
        
    print(f"\nTotal historical daily markets found: {matched}")

fetch_historical_daily_weather()
