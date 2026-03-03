import asyncio
import httpx
import json

TIGHT_KEYWORDS = ['high temperature', 'low temperature', 'daily high', 'daily low', 'degrees', 'fahrenheit', 'celsius']

def is_temp_market(item):
    s = ' '.join([item.get('question', ''), item.get('description', '')]).lower()
    return any(kw in s for kw in TIGHT_KEYWORDS)

async def check():
    found = []
    async with httpx.AsyncClient(timeout=30) as client:
        for offset in range(0, 5000, 100):
            try:
                resp = await client.get('https://gamma-api.polymarket.com/markets', params={
                    'limit': 100, 'offset': offset,
                    'closed': 'true',
                    'end_date_min': '2023-01-01T00:00:00Z',
                    'end_date_max': '2024-12-31T23:59:59Z',
                })
                data = resp.json()
                if not data: break
                for m in data:
                    if is_temp_market(m):
                        found.append({
                            'question': m.get('question', '')[:120],
                            'endDate': m.get('endDate', ''),
                            'slug': m.get('slug', '')
                        })
                if len(data) < 100: break
            except Exception: pass
            
    with open('historical_temp_markets.json', 'w') as f:
        json.dump(found, f, indent=2)
    print(f'Found {len(found)} historical temperature markets (2023-2024)')
    for m in found[:10]:
        print(f"  [{m.get('endDate', '')[:10]}] {m.get('question', '')[:80]}")

if __name__ == "__main__":
    asyncio.run(check())
