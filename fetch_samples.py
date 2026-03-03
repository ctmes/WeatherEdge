import asyncio
import httpx
import json

async def fetch_samples():
    base = "https://gamma-api.polymarket.com"
    
    print("--- WEATHER MARKETS ---")
    async with httpx.AsyncClient() as client:
        # Search for markets with "temperature"
        resp = await client.get(f"{base}/markets", params={"limit": 50, "active": "true", "query": "temperature"})
        markets = resp.json()
        for m in markets:
            print(f"Q: {m.get('question')}")
            print(f"D: {m.get('description')[:50]}...")
            print("-" * 20)

if __name__ == "__main__":
    asyncio.run(fetch_samples())
