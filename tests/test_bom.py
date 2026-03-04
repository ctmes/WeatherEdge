import asyncio
import logging
from weatheredge.forecast import fetch_forecast

async def test_bom():
    logging.basicConfig(level=logging.INFO)
    # Sydney coordinates
    lat, lon = -33.9461, 151.1772
    print(f"Fetching blended ensemble for Sydney ({lat}, {lon})...")
    # Try with 'bom_access_global' (ensemble)
    results = await fetch_forecast(lat, lon)


    
    if not results:
        print("No results returned.")
    else:
        for r in results[:3]:
            print(f"Date: {r.valid_date}")
            print(f"  TMax: {r.tmax_mean:.2f} +/- {r.tmax_std:.2f}")
            print(f"  Models: {r.models_used}")

    models_to_try = ["bom_access", "access_ge", "access_g", "bom_global", "bom_access_global", "access_global"]
    import httpx
    async with httpx.AsyncClient() as client:
        for model in models_to_try:
            print(f"Testing model: {model}...")
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "temperature_2m_max",
                    "models": model,
                    "timezone": "auto"
                }
            )
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"  SUCCESS! Data: {resp.json().get('daily', {}).get('temperature_2m_max', [])[:2]}")




if __name__ == "__main__":
    asyncio.run(test_bom())
