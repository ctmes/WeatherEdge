import json
import asyncio
from datetime import date
from weatheredge.polymarket import _raw_to_parsed_market
from weatheredge.backtest_fetchers import fetch_historical_forecast, extract_resolved_outcome
from weatheredge.pipeline import _process_market

with open('temp_markets.json', 'r') as f:
    markets = json.load(f)

async def run():
    # Only test the first market
    m = markets[0]
    
    # Fake outcomePrices for testing since temp_markets.json lacks them
    m["outcomePrices"] = '["0.6", "0.4"]'
    
    parsed = _raw_to_parsed_market(m)
    print(f"Parsed market: {parsed.question}")
    print(f"Bins: {parsed.bins}")
    print(f"Station: {parsed.station_id}")
    
    fc = await fetch_historical_forecast(0.0, 0.0, date(2024, 12, 31), model="ecmwf_ifs", timezone="UTC")
    print(f"Forecast: Tmax={fc.tmax_mean} +/- {fc.tmax_std}")
    
    fc_by_date = {date(2024, 12, 31): fc}
    rg_by_date = {}
    
    signals, detail = _process_market(
        market=parsed,
        fc_by_date=fc_by_date,
        rg_by_date=rg_by_date,
        calibration=None,
        bankroll=10000.0,
        eval_date=date(2024, 12, 31)
    )
    
    print("--- Detail ---")
    for k, v in detail.items():
        print(f"{k}: {v}")
    
    print("--- Signals ---")
    for s in signals:
        print(s)
        
asyncio.run(run())
