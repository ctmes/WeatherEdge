# Project Specification: WeatherEdge — Polymarket Weather Trading Bot
### Single Source of Truth — AI IDE Reference Document
**Version:** 1.0  
**Date:** March 2026  
**Author:** August Labs  
**Status:** Build Phase 1 — Paper Trading  

---

## 1. PRODUCT OVERVIEW

### What It Is
WeatherEdge is an automated trading bot that identifies and exploits mispriced weather prediction markets on Polymarket. It compares calibrated probabilistic weather forecasts against Polymarket's implied market probabilities, flags divergences above a threshold, and executes or suggests trades accordingly.

### One Sentence Description
"Find the gap between what the weather models say and what Polymarket prices imply — bet the gap."

### The Core Insight
Polymarket weather markets are priced by a mix of sharp bettors, dumb money, and bots. The dumb money consistently misprices markets because they use generic city-level forecasts rather than reading the exact resolution rules — which station, which metric, which threshold, which timezone. This creates systematic, exploitable edges of 2-5% on individual markets.

The edge compounds when:
- New forecast model runs drop every 6 hours and markets are slow to reprice
- Dumb money bets emotionally on extreme outcomes
- Markets are thin and illiquid with few sharp participants
- Resolution criteria are ambiguous or hyperlocal

### The Opportunity
- 42+ active daily temperature markets on Polymarket at any given time
- $50,000-$200,000 volume per market
- Top weather traders (gopfan2, neobrother, Hans323) have made $20,000-$2,000,000+ in documented profits
- The edge is real, systematic, and buildable with public data and open source models

---

## 2. HOW POLYMARKET WEATHER MARKETS WORK

### Market Structure
Polymarket weather markets are structured as multiple choice questions:

**Example:** "What will the high temperature in Wellington on March 5 be?"
- Under 18°C — implied probability 8% ($0.08)
- 18-20°C — implied probability 22% ($0.22)
- 20-22°C — implied probability 35% ($0.35)
- 22-24°C — implied probability 25% ($0.25)
- Over 24°C — implied probability 10% ($0.10)

All probabilities sum to ~100% (minus fees/spread). Each option trades as a binary outcome share priced $0.00-$1.00. Correct outcome pays $1.00.

### Resolution Rules — The Critical Detail
Every market resolves against a **specific weather station** — almost always a Weather Underground (Wunderground) airport station. The resolution rules specify:
- Exact station ID (e.g., NZWN for Wellington Airport)
- Exact metric (daily high, daily low, precipitation total)
- Exact threshold values
- Exact timezone for the measurement period
- Data source (always verify — usually Weather Underground)

**This is where the edge lives.** Most bettors use general city forecasts. Markets resolve against hyperlocal airport station data. These can differ by 1-3°C depending on urban heat islands, coastal effects, elevation, and microclimate.

### Fee Structure
- Polymarket charges ~2% on winning trades
- Factor this into all edge calculations
- Minimum viable edge before betting: >4% to cover fees and spread

---

## 3. THE EDGE — WHERE MONEY IS MADE

### Edge Type 1: Forecast vs Market Divergence
**What it is:** Weather models give one probability distribution. Polymarket implies a different one. Bet the gap.

**Example:**
- GFS/ECMWF ensemble says 70% chance high temp is 20-22°C in Wellington
- Polymarket prices that bin at 45% ($0.45)
- True edge: ~25% mispricing minus fees = ~23% net edge
- Buy YES on 20-22°C bin

**How to find it:** Pull model probability distribution → compare to market implied probabilities → flag divergences >5%

### Edge Type 2: Model Update Repricing Lag
**What it is:** Forecast models update every 6 hours (GFS) and 12 hours (ECMWF). When a new run significantly shifts the forecast, markets are slow to reprice — sometimes by 1-2 hours.

**Example:**
- 6am GFS run shifts Wellington forecast from 21°C to 24°C high
- Market still priced for 21°C distribution
- Window: ~60-90 minutes before sharp bots catch up
- This is the highest value edge but requires speed and automation

**How to find it:** Monitor model run completion → compare new run vs previous run → if significant shift, immediately check market prices → execute before repricing

### Edge Type 3: Station vs City Forecast Divergence
**What it is:** Wellington Airport (NZWN) consistently runs 1-2°C cooler than Wellington city centre due to coastal exposure. Most bettors use city forecasts. Markets resolve against the airport station.

**Example:**
- City forecast: 23°C high
- Airport station historical bias: -1.5°C vs city
- Calibrated airport forecast: 21.5°C high
- Market priced for 23°C distribution → overpriced high bins, underpriced mid bins

**How to find it:** Build historical bias calibration per station (see Section 6)

### Edge Type 4: Thin Market Mispricing
**What it is:** Low volume markets with few participants have wide spreads and poorly calibrated prices set by one or two bettors.

**How to find it:** Filter for markets with <$10,000 total volume — higher variance but larger mispricings

### Edge Type 5: Dumb Money Emotional Bias
**What it is:** Bettors consistently overbet extreme outcomes (very hot, very cold) because they're memorable and exciting. Mid-range bins are systematically underpriced.

**How to find it:** Compare market prices to historical base rates for each city — if extreme bins are priced above their historical frequency, fade them

---

## 4. FORECAST MODELS

### Model Comparison

| Model | Update Frequency | Resolution | Strengths | Weaknesses | Best Use |
|-------|-----------------|------------|-----------|------------|----------|
| ECMWF IFS | 12 hours | ~9km | Top global accuracy, best 1-5 day | Paid API, slower updates | Primary forecast baseline |
| GFS | 6 hours | ~27km | Free, frequent updates, US strong | Coarser resolution | Update lag detection |
| HRRR | 1 hour | ~3km | Hourly updates, very short range | US only, <48hr | Same-day markets |
| GraphCast/AIFS | 12 hours | ~25km | Excellent mid-range, free | Biases vs stations, less short-term | Secondary validation |
| Open-Meteo Ensemble | 6 hours | Variable | Free, combines multiple models | Averaged, less sharp | Quick sanity check |

### Recommended Stack
1. **Primary:** ECMWF IFS for 1-5 day forecasts (most accurate for daily highs/lows)
2. **Secondary:** GFS for 6-hourly update detection and US markets
3. **Tertiary:** GraphCast/AIFS as validation layer
4. **Ensemble:** Average all three for highest Sharpe ratio (+1-2% edge vs single model)

### Model Access

**ECMWF:**
- Free tier available via ECMWF Open Data
- URL: `https://data.ecmwf.int/forecasts/`
- Parameters needed: `2m_temperature`, `10m_wind_speed`, `total_precipitation`
- Update schedule: 00z and 12z runs, available ~6 hours after run time

**GFS:**
- Fully free via NOAA
- URL: `https://nomads.ncep.noaa.gov/`
- GRIB2 format — use `cfgrib` or `pygrib` to parse
- Update schedule: 00z, 06z, 12z, 18z runs

**GraphCast:**
- Open source via Google DeepMind
- Available via WeatherBench2 or direct model weights
- Also accessible via `open-meteo.com` API (free, no key required)

**Open-Meteo (recommended for speed):**
```
GET https://api.open-meteo.com/v1/forecast
  ?latitude={LAT}
  &longitude={LON}
  &daily=temperature_2m_max,temperature_2m_min,precipitation_sum
  &models=ecmwf_ifs,gfs_seamless,gem_seamless
  &timezone={TIMEZONE}
  &forecast_days=7
```

---

## 5. POLYMARKET API

### Authentication
Polymarket uses the CLOB (Central Limit Order Book) API. No authentication required for read operations.

### Key Endpoints

**Get all markets:**
```
GET https://clob.polymarket.com/markets
  ?next_cursor={cursor}
  &limit=100
```

**Get specific market:**
```
GET https://clob.polymarket.com/markets/{condition_id}
```

**Get order book (current prices):**
```
GET https://clob.polymarket.com/book
  ?token_id={token_id}
```

**Get market prices:**
```
GET https://clob.polymarket.com/midpoints
  ?token_ids={token_id_1},{token_id_2}
```

### Filtering Weather Markets
Weather markets have consistent naming patterns. Filter by:
- Title contains "temperature", "high", "low", "precipitation", "rainfall"
- Category tag: "weather"
- Active status: `active: true`

### Key Data Fields
```json
{
  "condition_id": "unique market identifier",
  "question": "What will the high temperature in Wellington be on March 5?",
  "outcomes": ["Under 18", "18-20", "20-22", "22-24", "Over 24"],
  "outcomePrices": ["0.08", "0.22", "0.35", "0.25", "0.10"],
  "volume": "125000",
  "endDate": "2026-03-05T23:59:00Z",
  "description": "Resolves based on Weather Underground station NZWN..."
}
```

### Parsing Resolution Rules
The `description` field contains the resolution rules in plain text. Use regex or LLM parsing to extract:
- Station ID (pattern: `[A-Z]{2,4}[0-9A-Z]+` e.g., NZWN, KORD, EGLL)
- Metric (high temp, low temp, precipitation)
- Threshold values
- Timezone

---

## 6. STATION CALIBRATION — THE REAL MOAT

### Why This Matters
This is the most defensible part of the system. Anyone can pull ECMWF forecasts. Not everyone has built a calibration layer that accounts for each specific station's historical bias vs model output.

### Data Sources for Historical Station Data

**Weather Underground Station History:**
```
GET https://api.weather.com/v1/location/{STATION_ID}/observations/historical.json
  ?apiKey={WU_API_KEY}
  &units=m
  &date={YYYYMMDD}
```
Note: Weather Underground API requires a key — free tier available

**NOAA Climate Data Online (free, no key):**
```
GET https://www.ncdc.noaa.gov/cdo-web/api/v2/data
  ?datasetid=GHCND
  &stationid=GHCND:{STATION_ID}
  &datatypeid=TMAX,TMIN,PRCP
  &startdate={START}
  &enddate={END}
```

**Iowa Environmental Mesonet (free, no key):**
```
GET https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py
  ?station={STATION_ID}
  &data=tmpf,dwpf,relh,drct,sknt,precip
  &year1={Y}&month1={M}&day1={D}
  &year2={Y}&month2={M}&day2={D}
  &tz=UTC&format=comma&latlon=yes
```

### Calibration Methodology

For each station, build a calibration dataset:

1. **Pull 2 years of historical observations** from the exact resolution station
2. **Pull historical ECMWF/GFS forecasts** for the same period (use reanalysis data — ERA5 for ECMWF)
3. **Calculate bias:** `station_actual - model_forecast` for each day
4. **Segment by:**
   - Season (winter/summer bias differs)
   - Wind direction (coastal stations run cooler in onshore flow)
   - Time of year
   - Forecast lead time (bias increases with longer lead times)
5. **Build correction factor:** Apply learned bias to future forecasts

**Example calibration result:**
```python
station_calibration = {
  "NZWN": {  # Wellington Airport
    "summer_tmax_bias": -1.4,  # Airport runs 1.4°C cooler than ECMWF in summer
    "winter_tmax_bias": -0.8,
    "onshore_wind_correction": -0.6,
    "lead_time_degradation": 0.15  # per day beyond day 1
  }
}
```

### ERA5 Reanalysis Data (for building historical calibration)
- Free via Copernicus Climate Data Store
- URL: `https://cds.climate.copernicus.eu/`
- Requires free account registration
- Python client: `import cdsapi`

---

## 7. PROBABILITY CONVERSION

### From Forecast to Probability Distribution

ECMWF and GFS give point forecasts and ensemble spreads. Convert to probability distributions for each market bin:

```python
from scipy import stats
import numpy as np

def forecast_to_probabilities(forecast_mean, forecast_std, bins):
    """
    Convert a forecast mean and standard deviation to 
    probabilities for each market bin.
    
    bins: list of tuples e.g. [(-inf, 18), (18, 20), (20, 22), (22, 24), (24, inf)]
    """
    distribution = stats.norm(loc=forecast_mean, scale=forecast_std)
    probabilities = []
    
    for low, high in bins:
        if low == float('-inf'):
            prob = distribution.cdf(high)
        elif high == float('inf'):
            prob = 1 - distribution.cdf(low)
        else:
            prob = distribution.cdf(high) - distribution.cdf(low)
        probabilities.append(prob)
    
    return probabilities
```

### From Market Prices to Implied Probabilities
Polymarket prices are already implied probabilities — a price of $0.35 implies 35% probability. Account for the overround (sum > 100%) by normalising:

```python
def normalise_market_probs(prices):
    total = sum(prices)
    return [p / total for p in prices]
```

### Edge Calculation
```python
def calculate_edge(model_prob, market_prob, fee=0.02):
    """
    Calculate the betting edge for a single outcome bin.
    Positive edge = bet YES. Negative edge = consider NO.
    """
    raw_edge = model_prob - market_prob
    net_edge = raw_edge - fee
    return net_edge

def kelly_fraction(edge, odds=1.0):
    """
    Kelly criterion for position sizing.
    Use fractional Kelly (0.25x) for safety.
    """
    if edge <= 0:
        return 0
    full_kelly = edge / odds
    fractional_kelly = full_kelly * 0.25  # 25% Kelly
    return min(fractional_kelly, 0.10)  # Cap at 10% bankroll per cluster
```

---

## 8. SYSTEM ARCHITECTURE

### High Level Components

```
┌─────────────────────────────────────────────────────┐
│                    SCHEDULER                         │
│         Runs every 30 minutes                        │
└──────────────────┬──────────────────────────────────┘
                   │
       ┌───────────┴───────────┐
       ▼                       ▼
┌──────────────┐      ┌─────────────────┐
│  POLYMARKET  │      │  FORECAST       │
│  SCRAPER     │      │  PULLER         │
│              │      │                 │
│ - Active     │      │ - Open-Meteo    │
│   markets    │      │ - GFS direct    │
│ - Prices     │      │ - ECMWF direct  │
│ - Resolution │      │ - Model ensemble│
│   rules      │      │                 │
└──────┬───────┘      └────────┬────────┘
       │                       │
       └───────────┬───────────┘
                   ▼
┌─────────────────────────────────────────────────────┐
│                 STATION MATCHER                      │
│  Parse resolution rules → extract station ID        │
│  Look up station coordinates and calibration data   │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│              CALIBRATION ENGINE                      │
│  Apply station-specific bias corrections             │
│  Apply seasonal adjustments                          │
│  Apply lead-time degradation                         │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│           PROBABILITY CONVERTER                      │
│  Convert calibrated forecast to bin probabilities   │
│  Normalise market implied probabilities              │
│  Calculate edge per bin                              │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│              SIGNAL FILTER                           │
│  Filter: edge > 5% threshold                        │
│  Filter: sufficient market liquidity                │
│  Filter: not within 2 hours of resolution           │
│  Rank by expected value                             │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│           POSITION SIZER (Kelly)                     │
│  Calculate fractional Kelly bet size                 │
│  Apply per-market caps                              │
│  Apply total exposure caps                          │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│         PAPER TRADE LOGGER / EXECUTOR                │
│  Month 1-2: Log to database, no real execution      │
│  Month 3+: Execute via Polymarket API               │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│              DASHBOARD / REPORTING                   │
│  Active positions, P&L, Brier scores, edge decay    │
└─────────────────────────────────────────────────────┘
```

### Tech Stack
- **Language:** Python 3.11+
- **Scheduler:** APScheduler or cron
- **Database:** PostgreSQL (positions, calibration data, historical results)
- **ORM:** SQLAlchemy
- **HTTP:** httpx (async)
- **Data processing:** pandas, numpy, scipy
- **Weather parsing:** cfgrib (GRIB2), xarray
- **Dashboard:** Streamlit (simple, fast to build)
- **Deployment:** Docker on any VPS (DigitalOcean, Hetzner)

---

## 9. DATABASE SCHEMA

```sql
-- Markets scraped from Polymarket
CREATE TABLE markets (
    id VARCHAR PRIMARY KEY,           -- Polymarket condition_id
    question TEXT,
    station_id VARCHAR,               -- Extracted e.g. NZWN
    metric VARCHAR,                   -- tmax, tmin, precip
    resolution_date DATE,
    timezone VARCHAR,
    bins JSONB,                       -- [{low: -inf, high: 18}, ...]
    created_at TIMESTAMP DEFAULT NOW()
);

-- Current and historical market prices
CREATE TABLE market_prices (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR REFERENCES markets(id),
    prices JSONB,                     -- {bin_index: implied_prob}
    recorded_at TIMESTAMP DEFAULT NOW()
);

-- Forecast snapshots per station per model run
CREATE TABLE forecasts (
    id SERIAL PRIMARY KEY,
    station_id VARCHAR,
    model VARCHAR,                    -- ecmwf, gfs, graphcast
    run_time TIMESTAMP,
    valid_date DATE,
    tmax_mean FLOAT,
    tmax_std FLOAT,
    tmin_mean FLOAT,
    tmin_std FLOAT,
    precip_mean FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Station calibration data
CREATE TABLE station_calibration (
    station_id VARCHAR PRIMARY KEY,
    latitude FLOAT,
    longitude FLOAT,
    elevation FLOAT,
    tmax_bias_summer FLOAT,
    tmax_bias_winter FLOAT,
    tmin_bias_summer FLOAT,
    tmin_bias_winter FLOAT,
    lead_time_degradation FLOAT,
    last_updated TIMESTAMP
);

-- Trade signals (paper and real)
CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR REFERENCES markets(id),
    bin_index INTEGER,
    model_prob FLOAT,
    market_prob FLOAT,
    edge FLOAT,
    kelly_fraction FLOAT,
    bet_size_usd FLOAT,
    signal_type VARCHAR,              -- paper, real
    created_at TIMESTAMP DEFAULT NOW()
);

-- Trade results for performance tracking
CREATE TABLE results (
    id SERIAL PRIMARY KEY,
    signal_id INTEGER REFERENCES signals(id),
    resolved_bin INTEGER,
    won BOOLEAN,
    pnl_usd FLOAT,
    resolved_at TIMESTAMP
);

-- Brier scores per model per market for calibration tracking
CREATE TABLE brier_scores (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR,
    model VARCHAR,
    brier_score FLOAT,
    calculated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 10. POSITION SIZING AND RISK MANAGEMENT

### Kelly Criterion Implementation
Use 25% fractional Kelly to reduce variance:

```python
MAX_SINGLE_MARKET_EXPOSURE = 0.10   # 10% bankroll max per market cluster
MAX_TOTAL_EXPOSURE = 0.60           # 60% bankroll max deployed at once
MIN_EDGE_THRESHOLD = 0.05           # 5% minimum edge before betting
MIN_LIQUIDITY = 5000                # $5,000 minimum market volume
```

### Bankroll Management Phases

**Phase 1 (Month 1-2): Paper trading**
- Zero real money
- Log every signal as if executed
- Track paper P&L vs actual outcomes
- Goal: validate edge is real before risking capital

**Phase 2 (Month 2-3): Micro betting**
- $200-500 total exposure
- Max $50 per market
- Testing execution, fills, fees
- Goal: confirm paper results translate to real money

**Phase 3 (Month 3-6): Scaled betting**
- $1,000-3,000 total exposure
- Max $200 per market cluster
- Full Kelly sizing within caps
- Goal: compound and validate at scale

**Phase 4 (Month 6+): Full deployment**
- Up to $10,000 bankroll
- Full position sizing per Kelly
- Automated execution
- Goal: maximum sustainable returns

### Black Swan Protection
Weather has fat tail risk — model consensus fails during convective events, rapid cyclogenesis, and unusual synoptic patterns. Rules:

- **Never bet** when GFS and ECMWF disagree by >3°C
- **Reduce sizing by 50%** when ensemble spread is >4°C
- **Exit immediately** on 20% adverse move from entry price
- **No betting** within 6 hours of resolution on volatile weather regimes

---

## 11. PERFORMANCE TRACKING

### Key Metrics to Track

**Calibration metrics:**
- **Brier Score** — measures probability calibration (lower is better, 0 is perfect)
- **Reliability diagram** — are your 70% predictions winning 70% of the time?
- **Resolution** — how sharp are your forecasts vs base rates?

**Financial metrics:**
- ROI per market type
- ROI per city/station
- ROI per forecast model
- Edge decay over time (are edges shrinking as competition increases?)
- Sharpe ratio on returns

**Operational metrics:**
- Signal count per day
- Average edge per signal
- Win rate by bin type (extreme vs mid-range)
- Execution quality (did you get filled at expected price?)

### Brier Score Calculation
```python
def brier_score(predicted_probs, actual_outcome_index, n_outcomes):
    """
    Calculate Brier score for a single market resolution.
    Lower is better. 0 = perfect. 2 = worst possible.
    """
    actual = [0] * n_outcomes
    actual[actual_outcome_index] = 1
    return sum((p - a) ** 2 for p, a in zip(predicted_probs, actual))
```

---

## 12. BUILD PHASES

### Phase 1 — Core Pipeline (Weeks 1-2)
**Goal:** Working data pipeline, no betting logic yet

- [ ] Polymarket API scraper — pull all active weather markets
- [ ] Resolution rule parser — extract station ID, metric, thresholds from description text
- [ ] Station lookup table — map station IDs to coordinates
- [ ] Open-Meteo forecast puller — basic temperature forecasts per station
- [ ] Database setup — PostgreSQL with schema from Section 9
- [ ] Basic dashboard — Streamlit showing active markets and current prices

**Deliverable:** Can see all active weather markets and their current prices alongside basic forecasts

### Phase 2 — Calibration Layer (Weeks 2-3)
**Goal:** Calibrated probabilities per station

- [ ] Historical station data puller — Iowa Mesonet or NOAA CDO
- [ ] ERA5 reanalysis data integration for historical model comparison
- [ ] Bias calculation per station per season
- [ ] Calibrated forecast generator — apply bias corrections to raw model output
- [ ] Probability converter — forecast distribution to bin probabilities
- [ ] Edge calculator — model prob vs market implied prob

**Deliverable:** System outputs calibrated edge estimates per market bin

### Phase 3 — Paper Trading (Weeks 3-5)
**Goal:** Validate edge is real before risking money

- [ ] Signal generator — flag all edges >5%
- [ ] Kelly sizer — calculate recommended bet sizes
- [ ] Paper trade logger — record all signals as if executed
- [ ] Resolution tracker — check outcomes and record wins/losses
- [ ] Brier score calculator — track calibration quality
- [ ] Performance dashboard — P&L, win rate, edge by market type

**Deliverable:** 3-4 weeks of paper trading data showing real edge

### Phase 4 — Live Trading (Month 2-3)
**Goal:** Real money execution with small sizing

- [ ] Polymarket wallet integration
- [ ] Order execution via CLOB API
- [ ] Position tracker — open positions and exposure
- [ ] Auto-exit on 20% adverse move
- [ ] Alert system — Telegram or email for large signals
- [ ] Full monitoring dashboard

**Deliverable:** Automated live trading with real money

### Phase 5 — Optimisation (Month 3+)
**Goal:** Maximise edge and scale

- [ ] Multi-model ensemble averaging
- [ ] ECMWF direct integration (upgrade from Open-Meteo)
- [ ] Model run update detector — catch repricing windows
- [ ] Expand to non-US markets (Wellington, London, Tokyo)
- [ ] Backtesting engine — test strategy on historical markets
- [ ] Edge decay monitoring — detect when competition is catching up

---

## 13. CITIES AND STATIONS TO PRIORITISE

Start with these markets — highest volume, well documented resolution stations:

| City | Station ID | Notes |
|------|-----------|-------|
| Wellington, NZ | NZWN | Airport, coastal, runs cool vs city |
| London, UK | EGLL | Heathrow, well calibrated |
| New York, US | KNYC or KJFK | High volume, US market |
| Chicago, US | KORD | O'Hare Airport, volatile weather |
| Sydney, AU | YSSY | Good volume, familiar for AU dev |
| Melbourne, AU | YMML | High variability, good edge potential |
| Tokyo, JP | RJTT | Less competition, good volume |

---

## 14. EXPECTED RETURNS

### Conservative Projections (Starting $10,000 AUD bankroll)

| Period | Deployed Capital | Monthly ROI | Bankroll | Notes |
|--------|-----------------|-------------|----------|-------|
| Month 1 | Paper only | N/A | $10,000 | Calibration phase |
| Month 2 | $500 | Validation | $10,000 | Micro testing |
| Month 3 | $2,000 | 3-5% | $10,400 | Early real trading |
| Month 6 | $5,000 | 4-6% | $12,000-14,000 | Finding best markets |
| Month 12 | $10,000 | 5-8% | $17,000-22,000 | Full deployment |
| Month 18 | $15,000+ | 5-8% | $25,000-35,000 | Compounding |
| Month 24 | $20,000+ | 5-8% | $35,000-55,000 | Scaling up |

### Key Assumptions
- Edge of 3-5% per bet after fees on well-calibrated markets
- 25% fractional Kelly sizing
- Reinvestment of all profits
- No black swan blowup events
- Edge stays stable (competition doesn't fully arbitrage it away)

### Risk Scenarios
- **Best case (20% probability):** 3-4x bankroll in 24 months, $30-40k
- **Base case (50% probability):** 1.5-2.5x bankroll, $15-25k
- **Break even (20% probability):** Roughly flat after fees
- **Loss (10% probability):** Significant drawdown from bugs or bad weather events

---

## 15. INTEGRATION WITH FUTURE PRODUCTS

This bot is designed as a standalone module that can eventually be integrated into a broader AI-powered dashboard platform (August Labs modular dashboard). The weather intelligence layer specifically has applications in:

- **ContractorBridge:** Flagging weather-related project delays for site reporting
- **General market intelligence:** The edge-finding framework generalises to other Polymarket verticals (elections, economics)
- **Data product:** Calibrated hyperlocal weather probabilities as a standalone data feed

Keep the architecture modular and API-first so components can be extracted and reused.

---

## 16. IMMEDIATE NEXT STEPS

1. **This week:** Set up PostgreSQL database with schema from Section 9
2. **This week:** Build Polymarket scraper — pull all active weather markets
3. **This week:** Build resolution rule parser — extract station IDs
4. **Next week:** Integrate Open-Meteo API for basic forecasts
5. **Next week:** Build edge calculator — first version, no calibration yet
6. **Week 3:** Add calibration layer using Iowa Mesonet historical data
7. **Week 3-4:** Paper trade — log all signals, track outcomes
8. **Month 2:** Evaluate paper results honestly before risking real money

---

*This document is the single source of truth for WeatherEdge development. All implementation decisions should refer back to this specification. Update version number when making significant architectural changes.*