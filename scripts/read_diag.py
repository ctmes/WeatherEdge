import json
import codecs

with codecs.open('diag.json', 'r', 'utf-16le') as f:
    data = json.load(f)

print(f"Total Markets Found: {data.get('weather_markets_found')}")
for m in data.get('market_details', []):
    print(f"\n[{m.get('station_id')}] {m.get('question')}")
    if m.get('skip_reason'):
        print(f"  Skipped: {m.get('skip_reason')}")
    else:
        model_p = m.get('model_probs', [])
        mkt_p = m.get('market_probs', [])
        if model_p and mkt_p:
            edges = [round(mp - mk, 3) for mp, mk in zip(model_p, mkt_p)]
            print(f"  Model Probs: {model_p}")
            print(f"  Market Odds: {mkt_p}")
            print(f"  Raw Edges:   {edges}")
            print(f"  Signals:     {m.get('signals_found')}")
