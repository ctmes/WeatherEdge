"""
Diagnostic: test what Open-Meteo Historical Forecast API returns
for ECMWF ensemble members on a known station-date.

Usage: python diagnose_ensemble.py
"""
import asyncio
import httpx
import os

# Use the same base URL as the live system
API_KEY = os.environ.get("OPEN_METEO_API_KEY", "")
BASE = (
    "https://customer-historical-forecast-api.open-meteo.com/v1/forecast"
    if API_KEY
    else "https://historical-forecast-api.open-meteo.com/v1/forecast"
)

# London Heathrow — known good station, Feb 5 2026
LAT, LON = 51.4775, -0.4614
DATE = "2026-02-05"


async def check_member_vars():
    """Test whether the API returns per-member daily tmax."""
    member_vars = ",".join(f"temperature_2m_max_member{i:02d}" for i in range(51))
    params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": member_vars,
        "timezone": "auto",
        "start_date": DATE,
        "end_date": DATE,
        "models": "ecmwf_ifs_ensemble",
    }
    if API_KEY:
        params["apikey"] = API_KEY

    print(f"URL:   {BASE}")
    print(f"Model: ecmwf_ifs_ensemble")
    print(f"Vars:  temperature_2m_max_member00 … member50")
    print(f"Date:  {DATE}  Lat/Lon: {LAT}, {LON}")
    print()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(BASE, params=params)

    print(f"HTTP status: {resp.status_code}")
    if resp.status_code != 200:
        print("Response body:", resp.text[:500])
        return

    data = resp.json()
    daily = data.get("daily", {})

    # Collect member temperatures
    import numpy as np
    member_temps = []
    for i in range(51):
        key = f"temperature_2m_max_member{i:02d}"
        vals = daily.get(key, [])
        v = vals[0] if vals else None
        if v is not None:
            member_temps.append(float(v))

    print(f"Members returned with data: {len(member_temps)} / 51")

    if member_temps:
        arr = np.array(member_temps)
        print(f"Mean:   {arr.mean():.2f}°C")
        print(f"Std:    {arr.std():.2f}°C")
        print(f"Min:    {arr.min():.2f}°C")
        print(f"Max:    {arr.max():.2f}°C")
        print(f"Sample (first 5): {[round(t, 1) for t in member_temps[:5]]}")
    else:
        print("No member data returned — API does not support this variable/model combo.")

    # Also check what keys ARE in the daily response
    print(f"\nKeys present in 'daily': {list(daily.keys())[:10]}")


async def check_deterministic_fallback():
    """Check what the deterministic models return for the same date."""
    import numpy as np
    print("\n--- Deterministic model fallback ---")
    models = ["ecmwf_ifs", "gfs_seamless", "gem_seamless", "bom_access_global"]
    means = []
    for m in models:
        params = {
            "latitude": LAT,
            "longitude": LON,
            "daily": "temperature_2m_max",
            "timezone": "auto",
            "start_date": DATE,
            "end_date": DATE,
            "models": m,
        }
        if API_KEY:
            params["apikey"] = API_KEY
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(BASE, params=params)
            if resp.status_code == 200:
                vals = resp.json().get("daily", {}).get("temperature_2m_max", [])
                t = vals[0] if vals else None
                if t is not None:
                    means.append(float(t))
                    print(f"  {m:<25} {t:.1f}°C")
                else:
                    print(f"  {m:<25} no data")
            else:
                print(f"  {m:<25} HTTP {resp.status_code}")
        except Exception as e:
            print(f"  {m:<25} error: {e}")

    if len(means) > 1:
        arr = np.array(means)
        print(f"\n  Inter-model spread std: {arr.std():.2f}°C  (clamped to min 1.0 in fallback)")
    elif len(means) == 1:
        print("\n  Only 1 model returned data — fallback std defaults to 1.5")


asyncio.run(check_member_vars())
asyncio.run(check_deterministic_fallback())
