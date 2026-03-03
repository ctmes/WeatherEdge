import asyncio
from weatheredge.polymarket import fetch_weather_markets

async def show_unmatched():
    print("Fetching markets...")
    markets = await fetch_weather_markets(max_pages=50)
    
    unmatched = [m for m in markets if m.station_id is None]
    
    print(f"\nFound {len(unmatched)} unmatched markets (likely weather-related but no station):\n")
    for i, m in enumerate(unmatched, 1):
        print(f"{i:2}. Q: {m.question}")
        print(f"    D: {m.description[:100]}...")
        print("-" * 40)

if __name__ == "__main__":
    asyncio.run(show_unmatched())
