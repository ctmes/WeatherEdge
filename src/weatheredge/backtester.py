"""

Backtester orchestrator - replay resolved Polymarket weather markets against

historical forecasts, score predictions, and persist results.



Usage:

    python -m weatheredge backtest --start 2025-01-01 --end 2025-03-31

"""



from __future__ import annotations



import logging
from weatheredge import analytics

from dataclasses import dataclass

from datetime import date, datetime, timedelta

from typing import Any, TypedDict



from weatheredge.backtest_fetchers import (

    cache_fingerprint,

    extract_resolved_outcome,

    extract_winner_from_event,

    fetch_historical_forecast,

    fetch_historical_forecast_ensemble,

    fetch_resolved_daily_events,

    fetch_resolved_markets,

)

from weatheredge.config import TRADING

from weatheredge.db import get_session, init_db, upsert_market

from weatheredge.forecast import AtmosphericRegime

from weatheredge.models import (

    BacktestRun,

    BrierScore,

    EdgeSignal,

    ForecastResult,

    Market,

    ParsedMarket,

    Result,

    Signal,

)

from weatheredge.pipeline import _process_market

from weatheredge.probability import brier_score, normalise_market_probs

from weatheredge.stations import get_station



log = logging.getLogger("weatheredge.backtester")





# ---------------------------------------------------------------------------

# Config

# ---------------------------------------------------------------------------



class BacktestConfig(TypedDict, total=False):

    start_date: str     # YYYY-MM-DD

    end_date: str       # YYYY-MM-DD

    bankroll: float

    model: str          # ecmwf_ifs, gfs_seamless, etc.

    kelly_fraction: float

    use_ensemble: bool

    mode: str           # "brier_only" or "full_sim" (default: "brier_only")

    calibrations: dict   # {station_id: StationBias} — passed through to pipeline





# ---------------------------------------------------------------------------

# Public API

# ---------------------------------------------------------------------------



async def run_backtest(cfg: BacktestConfig) -> BacktestRun:

    """

    Execute a full backtesting pipeline:



    1. Create BacktestRun row (config JSON stored)

    2. Fetch resolved markets from Gamma -> upsert Market rows

    3. For each market: replay historical forecast

    4. Run _process_market() - same edge logic as live

    5. Score each signal: P&L + Brier score

    6. Commit all in one transaction, set finished_at

    """

    start = date.fromisoformat(cfg.get("start_date", "2025-01-01"))

    end = date.fromisoformat(cfg.get("end_date", "2025-12-31"))

    bankroll = cfg.get("bankroll", 10_000.0)

    model = cfg.get("model", "ecmwf_ifs")

    use_ensemble = cfg.get("use_ensemble", False)

    mode = cfg.get("mode", "brier_only")

    calibrations = cfg.get("calibrations", {})



    await init_db()



    # -- 1. Create BacktestRun ----------------------------------------

    log.info("Starting backtest: %s -> %s (model=%s)", start, end, model)



    async with get_session() as session:
        safe_cfg = dict(cfg)
        safe_cfg.pop("calibrations", None)
        bt_run = BacktestRun(
            started_at=datetime.utcnow(),
            config=safe_cfg,
            cache_fingerprint=cache_fingerprint(),
        )

        session.add(bt_run)

        await session.flush()  # get bt_run.id

        run_id = bt_run.id



    # -- 2. Fetch resolved markets ------------------------------------

    # We fetch both legacy "markets" (global anomalies) and new "daily events"

    legacy_market_pairs = await fetch_resolved_markets(start, end)

    daily_event_pairs = await fetch_resolved_daily_events(start, end)

    

    market_pairs = legacy_market_pairs + daily_event_pairs

    log.info("Found %d total resolved weather markets (%d legacy, %d daily events)", 

             len(market_pairs), len(legacy_market_pairs), len(daily_event_pairs))



    if not market_pairs:

        async with get_session() as session:

            from sqlalchemy import update

            await session.execute(

                update(BacktestRun)

                .where(BacktestRun.id == run_id)

                .values(finished_at=datetime.utcnow(), markets_count=0)

            )

        log.warning("No resolved markets found - backtest empty.")

        return bt_run



    # Upsert all markets into DB
    async with get_session() as session:
        for parsed, raw in market_pairs:
            try:
                # NOTE: Gamma returns post-resolution prices (winner≈1.0, losers≈0.0).
                # In brier_only mode this doesn't matter (we only score forecasts).
                # In full_sim mode these serve as a rough proxy for market odds.
                # We do NOT override with uniform 1/n — that destroys edge detection.

                # Determine winner based on whether it's an event or a flat market
                if "markets" in raw:
                    resolved_bin = extract_winner_from_event(raw)
                else:
                    resolved_bin = extract_resolved_outcome(raw)



                outcomes = parsed.outcomes

                resolved_label = outcomes[resolved_bin] if (

                    resolved_bin is not None and resolved_bin < len(outcomes)

                ) else None



                # Convert end_date string to datetime

                end_dt = None

                if parsed.end_date:

                    try:

                        end_dt = datetime.fromisoformat(

                            str(parsed.end_date).replace("Z", "+00:00")

                        )

                    except (ValueError, TypeError):

                        pass



                # Normalize bins for JSON safety (replace inf with large numbers)

                safe_bins = []

                for low, high in (parsed.bins or []):

                    l_val = low if low != float("-inf") else -999.0

                    h_val = high if high != float("inf") else 999.0

                    safe_bins.append([l_val, h_val])



                await upsert_market(session, {

                    "id": parsed.condition_id,

                    "question": parsed.question,

                    "station_id": parsed.station_id,

                    "metric": parsed.metric,

                    "resolution_date": parsed.resolution_date,

                    "timezone": parsed.timezone,

                    "bins": safe_bins,

                    "outcomes": parsed.outcomes,

                    "outcome_prices": parsed.outcome_prices,

                    "token_ids": parsed.token_ids,

                    "volume": parsed.volume,

                    "end_date": end_dt,

                    "description": parsed.description,

                    "resolved_outcome": resolved_label,

                })

            except Exception as e:

                log.warning("Failed to upsert market %s: %s", parsed.condition_id, e)



    # -- 3-5. Replay each market --------------------------------------

    all_signals: list[EdgeSignal] = []

    all_results: list[dict] = []  # (signal_data, result_data) pairs

    brier_scores: list[dict] = []

    markets_processed = 0
    rolling_bankroll = bankroll
    equity_curve = [bankroll]
    daily_pnl = {} # date -> sum(pnl)
    total_exposure = 0.0  # running total of open bets
    market_exposures: dict[str, float] = {}  # per-market running total
    brier_only = (mode == "brier_only")



    print(f"\n{' REPLAYING MARKETS ':^60}")

    print("-" * 60)



    for parsed, raw in market_pairs:

        try:

            # Skip markets without station (can't get forecast)

            if not parsed.station_id:

                continue



            station = get_station(parsed.station_id)

            if station is None:

                continue



            # Determine decision time: end_date - 24h

            if parsed.resolution_date:

                decision_date = parsed.resolution_date

            elif parsed.end_date:

                try:

                    end_dt = datetime.fromisoformat(str(parsed.end_date).replace("Z", "+00:00"))

                    decision_date = (end_dt - timedelta(hours=24)).date()

                except (ValueError, TypeError):

                    continue

            else:

                continue



            # -- 3. Replay forecast ---------------------------------------
            # We want the forecast FOR the resolution date, but we assume
            # we are making the decision on decision_date.
            target_date = parsed.resolution_date or decision_date

            if use_ensemble:
                forecast = await fetch_historical_forecast_ensemble(
                    station.latitude, station.longitude, target_date,
                    timezone=station.timezone,
                )
            else:
                forecast = await fetch_historical_forecast(
                    station.latitude, station.longitude, target_date,
                    model=model, timezone=station.timezone,
                )



            if forecast is None:

                continue



            forecast.station_id = parsed.station_id



            # -- 4. Run edge calculation (same logic as live) -------------

            fc_by_date = {decision_date: forecast}

            rg_by_date: dict[date, AtmosphericRegime] = {}

            station_cal = calibrations.get(parsed.station_id)

            if brier_only:
                # Skip edge/Kelly/P&L — just compute Brier below
                signals = []
            else:
                signals, _detail = _process_market(
                    parsed, fc_by_date, rg_by_date, station_cal, rolling_bankroll,
                    eval_date=decision_date,
                    current_market_exposure=market_exposures.get(parsed.condition_id, 0.0),
                    current_total_exposure=total_exposure,
                )



            # -- 5. Score each signal -------------------------------------

            if "markets" in raw:

                resolved_bin = extract_winner_from_event(raw)

            else:

                resolved_bin = extract_resolved_outcome(raw)



            if resolved_bin is None:

                continue



            markets_processed += 1

            

            # Live Ticker Header
            q_safe = parsed.question.encode("ascii", "ignore").decode("ascii")
            print(f"\n[{decision_date}] {q_safe[:50]}...")
            print(f"  Forecast: {forecast.tmax_mean:.1f}C (std={forecast.tmax_std:.1f})")

            # Brier score (always compute when possible, even if no signals)
            from weatheredge.probability import forecast_to_probabilities
            from weatheredge.polymarket import parse_bins_from_outcomes

            bins = parse_bins_from_outcomes(parsed.outcomes) if not parsed.bins else parsed.bins
            if bins and forecast:
                metric = parsed.metric or "tmax"
                if metric == "tmin" and forecast.tmin_mean is not None:
                    mean_val = forecast.tmin_mean
                    std_val = forecast.tmin_std or 2.0
                else:
                    mean_val = forecast.tmax_mean
                    std_val = forecast.tmax_std

                model_probs = forecast_to_probabilities(mean_val, std_val, bins)
                bs = brier_score(model_probs, resolved_bin)
                brier_scores.append({
                    "market_id": parsed.condition_id,
                    "model": model if not use_ensemble else "ensemble",
                    "brier": bs,
                })

            if not signals:
                print("  Signals:  None")
                continue

            for sig in signals:
                won = sig.bin_index == resolved_bin
                pnl = _calculate_pnl(sig, won)
                rolling_bankroll += pnl
                equity_curve.append(rolling_bankroll)
                
                daily_pnl[decision_date] = daily_pnl.get(decision_date, 0.0) + pnl

                # Track exposure
                total_exposure += sig.recommended_bet_usd
                market_exposures[parsed.condition_id] = (
                    market_exposures.get(parsed.condition_id, 0.0) + sig.recommended_bet_usd
                )

                # Color coding (ANSI)
                win_loss_label = "\033[92mWIN\033[0m" if won else "\033[91mLOSS\033[0m"
                
                print(f"  Signal:   {sig.bin_label} | Edge: {sig.net_edge:+.1%} | Bet: ${sig.recommended_bet_usd:.0f}")
                print(f"  Result:   {win_loss_label} | P&L: {pnl:+.2f} | Bankroll: ${rolling_bankroll:,.0f}")

                all_signals.append(sig)
                all_results.append({
                    "signal": sig,
                    "won": won,
                    "pnl": pnl,
                    "resolved_bin": resolved_bin,
                    "decision_date": decision_date,
                })

        except Exception as e:
            log.warning("Backtester failed to process market %s: %s", parsed.condition_id, e)

    

    print("\n" + "-" * 60)





    # -- 6. Summary Stats & Persist -----------------------------------
    total_pnl = sum(r["pnl"] for r in all_results)
    
    # Calculate baseline Brier for BSS (uniform dist: 1/n)
    baseline_briers = []
    for parsed, raw in market_pairs:
        num_bins = len(parsed.outcomes)
        if num_bins > 0:
            if "markets" in raw:
                r_bin = extract_winner_from_event(raw)
            else:
                r_bin = extract_resolved_outcome(raw)
            if r_bin is not None:
                uniform_probs = [1.0 / num_bins] * num_bins
                baseline_briers.append(brier_score(uniform_probs, r_bin))
    
    avg_baseline_brier = sum(baseline_briers) / len(baseline_briers) if baseline_briers else 1.0
    avg_brier = (
        sum(b["brier"] for b in brier_scores) / len(brier_scores)
        if brier_scores else None
    )
    
    bss = analytics.brier_skill_score(avg_brier, avg_baseline_brier) if avg_brier is not None else 0.0
    mdd = analytics.max_drawdown(equity_curve)
    
    # Extract daily returns for Sharpe
    dates_sorted = sorted(daily_pnl.keys())
    returns_list = []
    prev_equity = bankroll
    for d in dates_sorted:
        day_pnl = daily_pnl[d]
        returns_list.append(day_pnl / prev_equity if prev_equity > 0 else 0)
        prev_equity += day_pnl
    
    sharpe = analytics.sharpe_ratio(returns_list)
    
    low_ci, high_ci = analytics.win_rate_confidence_interval(
        sum(1 for r in all_results if r["won"]), 
        len(all_results)
    )

    async with get_session() as session:

        for record in all_results:

            sig = record["signal"]

            db_signal = Signal(

                market_id=sig.market_id,

                backtest_run_id=run_id,

                bin_index=sig.bin_index,

                model_prob=sig.model_prob,

                market_prob=sig.market_prob,
                
                slippage=sig.slippage,

                edge=sig.net_edge,

                kelly_fraction=sig.kelly_fraction,

                bet_size_usd=sig.recommended_bet_usd,

                signal_type="backtest",

                created_at=datetime.combine(record["decision_date"], datetime.min.time()),

            )

            session.add(db_signal)

            await session.flush()  # get db_signal.id



            session.add(Result(

                signal_id=db_signal.id,

                backtest_run_id=run_id,

                resolved_bin=record["resolved_bin"],

                won=record["won"],

                pnl_usd=record["pnl"],

                resolved_at=datetime.utcnow(),

            ))



        for bs_record in brier_scores:

            session.add(BrierScore(

                backtest_run_id=run_id,

                market_id=bs_record["market_id"],

                model=bs_record["model"],

                brier_score=bs_record["brier"],

            ))



        # Update BacktestRun with summary stats

        from sqlalchemy import update

        await session.execute(

            update(BacktestRun)

            .where(BacktestRun.id == run_id)

            .values(
                finished_at=datetime.utcnow(),
                markets_count=markets_processed,
                signals_count=len(all_signals),
                total_pnl=total_pnl,
                avg_brier=avg_brier,
                max_drawdown=mdd,
                sharpe_ratio=sharpe,
            )
        )



    log.info(
        "Backtest #%d complete: %d markets, %d signals, P&L=$%.2f, Brier=%.4f (BSS=%.2f)",
        run_id, markets_processed, len(all_signals),
        total_pnl, avg_brier or 0.0, bss,
    )
    
    log.info(
        "Performance: Sharpe=%.2f, MaxDD=%.1f%%, WinRate=[%.1f%% - %.1f%%]",
        sharpe, mdd * 100, low_ci * 100, high_ci * 100
    )



    # Reload and return

    async with get_session() as session:

        from sqlalchemy import select

        result = await session.execute(

            select(BacktestRun).where(BacktestRun.id == run_id)

        )

        return result.scalar_one()





# ---------------------------------------------------------------------------

# Internals

# ---------------------------------------------------------------------------



def _calculate_pnl(sig: EdgeSignal, won: bool) -> float:

    """

    Paper P&L for a single signal, accounting for slippage.



    Fill price = market_prob + slippage (capped at 0.99)
    Win:  gross_profit * (1 - fee)

    Loss: -bet_size

    """

    if won:

        fill_price = min(sig.market_prob + sig.slippage, 0.99)

        if fill_price > 0:

            gross = sig.recommended_bet_usd * (1.0 / fill_price - 1.0)

        else:

            gross = 0.0

        return gross * (1.0 - TRADING.polymarket_fee)

    else:

        return -sig.recommended_bet_usd

