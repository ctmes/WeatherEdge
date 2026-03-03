import json
import asyncio
from datetime import date
from weatheredge.polymarket import _is_weather_market, _raw_to_parsed_market
from weatheredge.backtest_fetchers import extract_resolved_outcome, fetch_historical_forecast

with open('temp_markets.json', 'r') as f:
    markets = json.load(f)

async def run():
    print(f"Loaded {len(markets)} markets")
    m = markets[0]
    is_w = _is_weather_market(m)
    parsed = _raw_to_parsed_market(m)
    st = parsed.station_id if parsed else None
    bins = parsed.bins if parsed else None
    res_bin = extract_resolved_outcome(m)
    print(f"Market: {m['question'][:40]} | is_w:{is_w} | st:{st} | bins:{bins} | res_bin:{res_bin}")
    
    # Test forecast
    fc = await fetch_historical_forecast(0.0, 0.0, date(2024, 12, 31), model="ecmwf_ifs", timezone="UTC")
    print(f"Forecast: {fc}")

asyncio.run(run())
