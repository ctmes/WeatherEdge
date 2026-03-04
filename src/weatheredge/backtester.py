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

    data_source: str     # "gamma" (default) or "pmxt" (archive.pmxt.dev)

    walk_forward: bool   # Enable monthly recalibration to prevent lookahead bias





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
    data_source = cfg.get("data_source", "gamma")
    walk_forward = cfg.get("walk_forward", False)

    # Cache for walk-forward calibrations: {(year, month): {station_id: StationBias}}
    wf_cal_cache: dict[tuple[int, int], dict[str, Any]] = {}



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

    if data_source == "pmxt":
        from weatheredge.pmxt_fetcher import fetch_pmxt_resolved_markets
        latency_s = cfg.get("latency_seconds", 60)
        market_pairs = await fetch_pmxt_resolved_markets(
            start, end, model_name=model, latency_seconds=latency_s
        )
        log.info("Found %d weather markets from pmxt archive", len(market_pairs))
    else:
        # Default: fetch from Gamma API
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

    from tqdm import tqdm
    for parsed, raw in tqdm(market_pairs, desc="Backtesting", unit="mkt", ncols=80):

        try:

            # Skip markets without station (can't get forecast)

            if not parsed.station_id:

                continue

            # Skip global climate anomaly markets — not city-level temperature
            if parsed.station_id == "GLOBAL":
                log.debug("Skipping global climate market: %s", parsed.question[:60])
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

            # -- 4. Calibration -------------------------------------------
            if walk_forward:
                wf_key = (decision_date.year, decision_date.month)
                if wf_key not in wf_cal_cache:
                    wf_cal_cache[wf_key] = {}
                
                if parsed.station_id not in wf_cal_cache[wf_key]:
                    from weatheredge.calibration import run_calibration
                    # Calibrate using data up to the day before making the decision
                    bias, _ = await run_calibration(
                        parsed.station_id, 
                        days=365, 
                        end_date=decision_date - timedelta(days=1)
                    )
                    wf_cal_cache[wf_key][parsed.station_id] = bias
                
                station_cal = wf_cal_cache[wf_key][parsed.station_id]
            else:
                station_cal = calibrations.get(parsed.station_id)

            if brier_only:
                # Skip edge/Kelly/P&L — just compute Brier below
                signals = []
            else:
                obs_t0 = raw.get("_pmxt_orderbook")
                signals, _detail = _process_market(
                    parsed, fc_by_date, rg_by_date, station_cal, rolling_bankroll,
                    eval_date=decision_date,
                    current_market_exposure=0.0,
                    current_total_exposure=0.0,
                    orderbooks=obs_t0,
                )
                
                # For actual slippage simulation in backtest:
                # If we are using pmxt, we can fetch an orderbook a few minutes later.
                # For simplicity in this loop, we'll re-use the t0 orderbook but 
                # maybe add a small "latency shift" if we had the full archive access.
                # In this Phase 1 backtester, we'll use the t0 observation 
                # as both the "signal" price and the "fill" price, but the _process_market
                # already separates estimated_slippage.



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
            
            # Print removed to prevent spamming tqdm bar. Handled below only on trade signals.

            # Brier score (always compute when possible, even if no signals)
            from weatheredge.probability import forecast_to_probabilities
            from weatheredge.polymarket import parse_bins_from_outcomes

            bins = parse_bins_from_outcomes(parsed.outcomes) if not parsed.bins else parsed.bins
            if bins and forecast:
                metric = parsed.metric or "tmax"
                # -- Probability Calculation (with Calibration) ---
                from weatheredge.calibration import apply_calibration, apply_calibration_std
                
                raw_mean = forecast.tmax_mean if parsed.metric == "tmax" else (forecast.tmin_mean or 0)
                raw_std = forecast.tmax_std if parsed.metric == "tmax" else (forecast.tmin_std or 1.5)
                
                if station_cal:
                    cal_mean = apply_calibration(raw_mean, station_cal, target_date, metric=parsed.metric)
                    cal_std = apply_calibration_std(raw_std, station_cal)
                else:
                    cal_mean = raw_mean
                    cal_std = raw_std

                model_probs = forecast_to_probabilities(
                    cal_mean,
                    cal_std,
                    bins,
                )
                bs = brier_score(model_probs, resolved_bin)

                # Baseline: Uniform probability (1/N)
                num_bins = len(bins)
                baseline_probs = [1.0 / num_bins] * num_bins
                bbs = brier_score(baseline_probs, resolved_bin)

                brier_scores.append({
                    "market_id": parsed.condition_id,
                    "station_id": parsed.station_id,
                    "model": model if not use_ensemble else "ensemble",
                    "brier": bs,
                    "baseline_brier": bbs,
                })

            if not signals:
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
                resolved_label = parsed.outcomes[resolved_bin] if resolved_bin < len(parsed.outcomes) else f"Bin {resolved_bin}"
                
                tqdm.write(f"\n[{decision_date}] {q_safe[:65]}")
                tqdm.write(f"  Forecast: {forecast.tmax_mean:.1f}°C (std={forecast.tmax_std:.1f})")
                tqdm.write(f"  Bet:      \033[1m{sig.bin_label}\033[0m @ {sig.model_prob:.0%} model vs {sig.market_prob:.0%} market")
                tqdm.write(f"  Slippage: {sig.slippage:.1%} | Edge: {sig.net_edge:+.1%} | Size: ${sig.recommended_bet_usd:.0f}")
                tqdm.write(f"  Actual:   {resolved_label} → {win_loss_label} | P&L: {pnl:+.2f} | Bankroll: ${rolling_bankroll:,.0f}")

                # For backtesting, we simulate actual fill as market_prob + slippage
                # where 'slippage' here is the one calculated by _process_market
                actual_fill = min((sig.market_prob or 0.5) + sig.slippage, 0.99)
                actual_slip = sig.slippage  # In backtest simple mode, they are the same
                
                all_signals.append(sig)
                all_results.append({
                    "signal": sig,
                    "won": won,
                    "pnl": pnl,
                    "resolved_bin": resolved_bin,
                    "decision_date": decision_date,
                    "resolved_label": resolved_label,
                    "forecast_mean": forecast.tmax_mean,
                    "actual_fill_price": actual_fill,
                    "actual_slippage": actual_slip,
                })

        except Exception as e:
            log.warning("Backtester failed to process market %s: %s", parsed.condition_id, e)

    
    print("\n" + "-" * 60)
    # -- Trade Detail Table -------------------------------------------
    if all_results:
        print(f"\n{'='*90}")
        print(f"{'TRADE LOG':^90}")
        print(f"{'='*90}")
        print(f"{'Date':<12} {'Market':<30} {'Bet On':<16} {'Actual':<16} {'P&L':>8} {'W/L':>5}")
        print(f"{'-'*12} {'-'*30} {'-'*16} {'-'*16} {'-'*8} {'-'*5}")
        for r in all_results:
            sig = r["signal"]
            q_short = sig.question.encode('ascii', 'ignore').decode('ascii')[:30]
            bet_label = sig.bin_label[:16]
            actual = r.get("resolved_label", f"Bin {r['resolved_bin']}")[:16]
            wl = "✓" if r["won"] else "✗"
            print(f"{r['decision_date']}  {q_short:<30} {bet_label:<16} {actual:<16} {r['pnl']:>+8.2f} {wl:>5}")
        print(f"{'='*90}")
        wins = sum(1 for r in all_results if r["won"])
        trade_pnl = sum(r["pnl"] for r in all_results)
        print(f"Total: {len(all_results)} trades, {wins} wins, {len(all_results)-wins} losses, P&L: ${trade_pnl:+.2f}")
        print(f"{'='*90}\n")

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

    # -- 5. PERSIST RESULTS -------------------------------------------
    bt_run.finished_at = datetime.utcnow()
    bt_run.markets_count = markets_processed
    bt_run.signals_count = len(all_signals)
    bt_run.total_pnl = total_pnl
    bt_run.avg_brier = avg_brier
    bt_run.max_drawdown = float(mdd)
    bt_run.sharpe_ratio = float(sharpe)

    async with get_session() as session:
        # Update BacktestRun in DB
        from sqlalchemy import update
        await session.execute(
            update(BacktestRun)
            .where(BacktestRun.id == run_id)
            .values(
                finished_at=bt_run.finished_at,
                markets_count=bt_run.markets_count,
                signals_count=bt_run.signals_count,
                total_pnl=bt_run.total_pnl,
                avg_brier=bt_run.avg_brier,
                max_drawdown=bt_run.max_drawdown,
                sharpe_ratio=bt_run.sharpe_ratio,
            )
        )
    return bt_run

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

