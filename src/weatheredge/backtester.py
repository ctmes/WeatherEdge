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

    check_cache_coverage,

    extract_resolved_outcome,

    extract_winner_from_event,

    fetch_historical_forecast,

    fetch_historical_forecast_ensemble,

    fetch_resolved_daily_events,

    fetch_resolved_markets,

)

from weatheredge.config import TRADING

from weatheredge.db import get_session, init_db, upsert_market, warn_stale_calibrations

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
    fidelity: str       # "snapshot" (default) or "tick"
    kelly_fraction: float

    use_ensemble: bool

    mode: str           # "brier_only" or "full_sim" (default: "brier_only")

    calibrations: dict   # {station_id: StationBias} — passed through to pipeline

    data_source: str     # "gamma" or "pmxt" (archive.pmxt.dev)
    latency_seconds: int

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
    fidelity = cfg.get("fidelity", "snapshot")
    calibrations = cfg.get("calibrations", {})
    data_source = cfg.get("data_source", "gamma")
    walk_forward = cfg.get("walk_forward", False)

    # Block: full_sim requires real pre-resolution market prices.
    # Gamma returns post-resolution prices (winner≈1.0, losers≈0.0) which
    # produce a uniform 1/N substitution (≈11% per bin for 9-bin markets),
    # making every market appear to have edge. This is not a valid simulation.
    if mode == "full_sim" and data_source == "gamma":
        raise ValueError(
            "Cannot run full_sim with --source gamma.\n"
            "Gamma returns post-resolution prices (winner≈1.0, losers≈0.0) which "
            "are substituted with uniform 1/N prices (≈11% per bin) — every market "
            "will show phantom edge and P&L results will be meaningless.\n\n"
            "Use --source clob for real pre-resolution prices (unlimited history):\n"
            "  weatheredge backtest --source clob --mode full_sim\n\n"
            "Or --source pmxt for richer orderbook data (Feb 2026+):\n"
            "  weatheredge backtest --source pmxt --mode full_sim\n\n"
            "For forecast accuracy only (Brier scores, no market prices needed):\n"
            "  weatheredge backtest --source gamma --mode brier_only"
        )

    # Cache for walk-forward calibrations: {(year, month): {station_id: StationBias}}
    wf_cal_cache: dict[tuple[int, int], dict[str, Any]] = {}



    await init_db()

    # -- 0. Pre-flight data freshness checks --------------------------
    # Check that the disk cache covers the full requested date range.
    # Missing tail days trigger a WARNING and will be fetched on demand.
    check_cache_coverage(start, end)

    # Check that DB calibrations (used when walk_forward=False) are fresh.
    # Stale calibrations are used with a warning rather than blocking the run.
    async with get_session() as session:
        await warn_stale_calibrations(session, max_age_days=14, context="backtest")

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
    elif data_source == "clob":
        from weatheredge.backtest_fetchers import fetch_clob_resolved_markets
        market_pairs = await fetch_clob_resolved_markets(start, end, model_name=model)
        log.info("Found %d weather markets enriched with CLOB prices-history", len(market_pairs))
    else:
        # data_source == "gamma": only valid for brier_only (ValueError raised above for full_sim)
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
    # Memory strategy: flush signals/results/brier to DB every FLUSH_BATCH markets
    # so we never hold more than FLUSH_BATCH records in RAM at once.  Running
    # aggregates (totals, equity curve) are maintained incrementally.
    FLUSH_BATCH = 50

    all_signals: list[EdgeSignal] = []  # flushed every FLUSH_BATCH markets
    all_results: list[dict] = []        # flushed every FLUSH_BATCH markets
    brier_scores: list[dict] = []       # flushed every FLUSH_BATCH markets
    trade_log: list[dict] = []          # compact summary kept for end-of-run print

    markets_processed = 0
    total_signals = 0  # cumulative count (updated on each flush)
    rolling_bankroll = bankroll
    equity_curve = [bankroll]
    daily_pnl = {}  # date -> sum(pnl)
    total_pnl_running = 0.0  # incremental; replaces sum-over-all_results at end
    total_exposure = 0.0  # running total of open bets
    market_exposures: dict[str, float] = {}  # per-market running total
    brier_only = (mode == "brier_only")
    _markets_since_flush = 0  # counter for batch flushing

    async def _flush_pending() -> None:
        """Write accumulated signals/results/brier to DB, then clear the lists."""
        nonlocal total_signals
        if not all_results and not brier_scores:
            return
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
                await session.flush()
                session.add(Result(
                    signal_id=db_signal.id,
                    backtest_run_id=run_id,
                    resolved_bin=record["resolved_bin"],
                    won=record["won"],
                    pnl_usd=record["pnl"],
                    actual_fill_price=record.get("actual_fill_price"),
                    actual_slippage=record.get("actual_slippage"),
                    resolved_at=datetime.utcnow(),
                ))
            for bs_record in brier_scores:
                session.add(BrierScore(
                    backtest_run_id=run_id,
                    market_id=bs_record["market_id"],
                    model=bs_record["model"],
                    brier_score=bs_record["brier"],
                ))
        total_signals += len(all_signals)
        all_signals.clear()
        all_results.clear()
        brier_scores.clear()



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

            # In full_sim mode with --source clob, skip markets where the CLOB
            # prices-history API had no data (too far in the past). Proceeding
            # with Gamma's post-resolution prices (1.0 / 0.0 / 0.0 …) would
            # produce only zero_price_bins and no signals — skip explicitly.
            if not brier_only and raw.get("_clob_no_data"):
                log.info(
                    "Skipping %s — no CLOB price history available (market too old)",
                    parsed.question[:60],
                )
                continue



            station = get_station(parsed.station_id)

            if station is None:

                continue



            # Determine decision time.
            # CLOB prices are captured at 18:00 UTC on the day BEFORE
            # resolution (see fetch_clob_resolved_markets). The decision is
            # made at that moment, so decision_date is resolution_date - 1.
            # This ensures lead_days=1 for forecast uncertainty and correct
            # DB timestamps — no look-ahead from same-day observations.

            if parsed.resolution_date:

                decision_date = parsed.resolution_date - timedelta(days=1)

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
                    decision_date=decision_date,
                )
            else:
                forecast = await fetch_historical_forecast(
                    station.latitude, station.longitude, target_date,
                    model=model, timezone=station.timezone,
                    decision_date=decision_date,
                )



            if forecast is None:

                continue



            forecast.station_id = parsed.station_id



            # -- 4. Run edge calculation (same logic as live) -------------
            # Key fc_by_date by the resolution date (= target_date), because
            # _process_market looks up fc_by_date[market.resolution_date].
            # decision_date is the day BEFORE target_date (see above).

            fc_by_date = {target_date: forecast}

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

                # Real pre-resolution prices are already set on parsed.outcome_prices
                # by the "clob" or "pmxt" fetchers. gamma+full_sim is blocked above.
                sim_parsed = parsed

                signals, _detail = _process_market(
                    sim_parsed, fc_by_date, rg_by_date, station_cal, rolling_bankroll,
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
            _markets_since_flush += 1

            # Live Ticker Header
            q_safe = parsed.question.encode("ascii", "ignore").decode("ascii")

            # Brier score (always compute when possible, even if no signals)
            from weatheredge.probability import forecast_to_probabilities
            from weatheredge.polymarket import parse_bins_from_outcomes

            bins = parse_bins_from_outcomes(parsed.outcomes) if not parsed.bins else parsed.bins
            if bins and forecast:
                # -- Probability Calculation (with Calibration) ---
                from weatheredge.calibration import apply_calibration, apply_calibration_std

                raw_mean = forecast.tmax_mean if parsed.metric == "tmax" else (forecast.tmin_mean or 0)
                raw_std = forecast.tmax_std if parsed.metric == "tmax" else (forecast.tmin_std or 1.5)

                if station_cal:
                    cal_mean = apply_calibration(raw_mean, station_cal, target_date, metric=parsed.metric)
                    # In ensemble mode the std comes from actual 51-member spread —
                    # the right uncertainty estimate already. Don't floor it with
                    # the deterministic-model RMSE; that inflates the distribution
                    # and raises the Brier score even when the mean is correct.
                    cal_std = raw_std if use_ensemble else apply_calibration_std(raw_std, station_cal)
                else:
                    cal_mean = raw_mean
                    cal_std = raw_std

                model_probs = forecast_to_probabilities(cal_mean, cal_std, bins)
                bs = brier_score(model_probs, resolved_bin)

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

            if signals:
                for sig in signals:
                    won = sig.bin_index == resolved_bin
                    if fidelity == "tick":
                        actual_fill = min(sig.market_prob + 0.005, 0.99)
                        actual_slip = 0.005
                    else:
                        actual_fill = min((sig.market_prob or 0.5) + sig.slippage, 0.99)
                        actual_slip = sig.slippage

                    pnl = _calculate_pnl(sig, won, override_fill=actual_fill)
                    rolling_bankroll += pnl
                    total_pnl_running += pnl
                    equity_curve.append(rolling_bankroll)

                    daily_pnl[decision_date] = daily_pnl.get(decision_date, 0.0) + pnl

                    total_exposure += sig.recommended_bet_usd
                    market_exposures[parsed.condition_id] = (
                        market_exposures.get(parsed.condition_id, 0.0) + sig.recommended_bet_usd
                    )

                    win_loss_label = "\033[92mWIN\033[0m" if won else "\033[91mLOSS\033[0m"
                    resolved_label = parsed.outcomes[resolved_bin] if resolved_bin < len(parsed.outcomes) else f"Bin {resolved_bin}"

                    tqdm.write(f"\n[{decision_date}] {q_safe[:65]}")
                    tqdm.write(f"  Forecast: {forecast.tmax_mean:.1f}\u00b0C (std={forecast.tmax_std:.1f})")
                    tqdm.write(f"  Bet:      \033[1m{sig.bin_label}\033[0m @ {sig.model_prob:.0%} model vs {sig.market_prob:.0%} market")
                    tqdm.write(f"  Fidelity: {fidelity.upper()} | Fill: {actual_fill:.3f} | Slip: {actual_slip:+.3f}")
                    tqdm.write(f"  Actual:   {resolved_label} -> {win_loss_label} | P&L: {pnl:+.2f} | Bankroll: ${rolling_bankroll:,.0f}")

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
                    # Keep a compact copy for the end-of-run trade log print
                    trade_log.append({
                        "date": decision_date,
                        "question": sig.question,
                        "bin_label": sig.bin_label,
                        "resolved_label": resolved_label,
                        "pnl": pnl,
                        "won": won,
                    })

            # -- Batch flush: write to DB and free memory every FLUSH_BATCH markets --
            if _markets_since_flush >= FLUSH_BATCH:
                await _flush_pending()
                _markets_since_flush = 0
                log.debug("Flushed batch to DB (%d markets processed so far)", markets_processed)

        except Exception as e:
            log.warning("Backtester failed to process market %s: %s", parsed.condition_id, e)

    # Flush any remaining pending records
    await _flush_pending()

    print("\n" + "-" * 60)
    # -- Trade Detail Table (from compact trade_log, not all_results) -
    if trade_log:
        print(f"\n{'='*90}")
        print(f"{'TRADE LOG':^90}")
        print(f"{'='*90}")
        print(f"{'Date':<12} {'Market':<30} {'Bet On':<16} {'Actual':<16} {'P&L':>8} {'W/L':>5}")
        print(f"{'-'*12} {'-'*30} {'-'*16} {'-'*16} {'-'*8} {'-'*5}")
        for r in trade_log:
            q_short = r["question"].encode('ascii', 'ignore').decode('ascii')[:30]
            bet_label = r["bin_label"][:16]
            actual = r["resolved_label"][:16]
            wl = "WIN" if r["won"] else "LOSS"
            print(f"{r['date']}  {q_short:<30} {bet_label:<16} {actual:<16} {r['pnl']:>+8.2f} {wl:>5}")
        print(f"{'='*90}")
        wins = sum(1 for r in trade_log if r["won"])
        print(f"Total: {len(trade_log)} trades, {wins} wins, {len(trade_log)-wins} losses, P&L: ${total_pnl_running:+.2f}")
        print(f"{'='*90}\n")

    # -- 6. Summary Stats & Persist -----------------------------------
    total_pnl = total_pnl_running
    
    # Calculate baseline Brier for BSS.
    # NOTE: brier_scores was flushed to DB in batches above so the in-memory
    # list is now empty.  Re-derive avg_baseline using 1/n uniform assumption
    # which doesn't require re-reading the whole dataset.  avg_brier is queried
    # from the DB below.
    avg_baseline_brier = 1.0  # uniform = 1/n; refined from DB query below
    avg_brier: float | None = None
    try:
        from sqlalchemy import select, func
        async with get_session() as session:
            # Fetch avg brier from DB for this run (already written in batches)
            row = await session.execute(
                select(func.avg(BrierScore.brier_score)).where(
                    BrierScore.backtest_run_id == run_id
                )
            )
            avg_brier_val = row.scalar()
            if avg_brier_val is not None:
                avg_brier = float(avg_brier_val)
    except Exception as e:
        log.warning("Could not compute avg brier from DB: %s", e)
    
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

    wins_total = sum(1 for r in trade_log if r["won"])
    low_ci, high_ci = analytics.win_rate_confidence_interval(
        wins_total,
        len(trade_log)
    )

    # NOTE: signals/results/brier were already written to DB in batches above.
    # Only the final BacktestRun summary stats remain to be persisted.

    # -- 5. PERSIST RESULTS -------------------------------------------
    bt_run.finished_at = datetime.utcnow()
    bt_run.markets_count = markets_processed
    bt_run.signals_count = total_signals
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
                signals_count=total_signals,
                total_pnl=bt_run.total_pnl,
                avg_brier=bt_run.avg_brier,
                max_drawdown=bt_run.max_drawdown,
                sharpe_ratio=bt_run.sharpe_ratio,
            )
        )
    return bt_run

# Internals

# ---------------------------------------------------------------------------



def _calculate_pnl(sig: EdgeSignal, won: bool, override_fill: float | None = None) -> float:
    """
    Paper P&L for a single signal, accounting for slippage.

    If override_fill is provided (e.g. from tick simulation), uses that.
    Otherwise falls back to market_prob + theoretical slippage.
    """
    if won:
        if override_fill is not None:
            fill_price = override_fill
        else:
            fill_price = min(sig.market_prob + sig.slippage, 0.99)

        if fill_price > 0:
            gross = sig.recommended_bet_usd * (1.0 / fill_price - 1.0)
        else:
            gross = 0.0

        return gross * (1.0 - TRADING.polymarket_fee)
    else:
        return -sig.recommended_bet_usd

