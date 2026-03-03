"""
Integration smoke tests — live API calls to verify connectivity.

These tests hit real external APIs and are skipped in CI.
Run with:  python -m pytest tests/test_integration.py -v -m integration
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_polymarket_markets_api():
    """Verify we can reach the Gamma API and get JSON back."""
    import httpx

    url = "https://gamma-api.polymarket.com/markets"
    params = {"limit": 5, "active": "true"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_open_meteo_forecast_api():
    """
    Verify we can fetch a forecast for Wellington (NZWN) from Open-Meteo.
    """
    from weatheredge.forecast import fetch_forecast

    results = await fetch_forecast(
        latitude=-41.3276,
        longitude=174.8050,
        timezone="Pacific/Auckland",
        forecast_days=3,
    )

    assert len(results) > 0
    r = results[0]
    # Temperature should be in a sane range (-50°C to +60°C)
    assert -50 < r.tmax_mean < 60
    assert r.tmax_std >= 0
