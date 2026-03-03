import asyncio
import httpx

async def find_all_weather():
    base = "https://gamma-api.polymarket.com"
    all_weather = []
    async with httpx.AsyncClient() as client:
        for offset in range(0, 4000, 100):
            resp = await client.get(f"{base}/markets", params={"limit": 100, "offset": offset, "active": "true", "closed": "false"})
            markets = resp.json()
            if not markets:
                break
            for m in markets:
                q = (m.get("question") or "").lower()
                d = (m.get("description") or "").lower()
                gt = (m.get("groupItemTitle") or "").lower()
                
                if "temperature" in q or "temperature" in d or "degrees" in q or "degrees" in d:
                    all_weather.append(m)
            
            if len(markets) < 100:
                break
                
    print(f"Found {len(all_weather)} markets containing 'temperature' or 'degrees'")
    for m in all_weather:
        print(f"Q: {m.get('question')}")
        print(f"D: {m.get('description')[:50]}...")
        print("-" * 10)

if __name__ == "__main__":
    asyncio.run(find_all_weather())
