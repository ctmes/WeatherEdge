"""

CLI entry point - run scans, diagnostics, calibration, paper trading, and dashboard.



Usage:

    python -m weatheredge scan

    python -m weatheredge scan --bankroll 5000 -v

    python -m weatheredge diagnose

    python -m weatheredge calibrate NZWN --days 90

    python -m weatheredge run               # start 30-min scheduler

    python -m weatheredge run --once        # single scan-and-log cycle

    python -m weatheredge resolve           # check resolved markets

    python -m weatheredge live-paper        # start real-time paper trading (WebSocket)

    python -m weatheredge dashboard         # launch Streamlit dashboard

"""



from __future__ import annotations



import argparse

import asyncio

import json

import logging

import sys



from weatheredge.pipeline import run_scan
from weatheredge.stations import get_station

def _run_live_paper(args: argparse.Namespace) -> None:
    from weatheredge.live_paper import run_live_paper
    asyncio.run(run_live_paper(
        bankroll=args.bankroll,
        update_interval=args.interval
    ))






def main() -> None:

    parser = argparse.ArgumentParser(

        prog="weatheredge",

        description="WeatherEdge - Polymarket weather trading bot",

    )

    subparsers = parser.add_subparsers(dest="command")



    # -- scan ----------------------------------------------------------

    scan_parser = subparsers.add_parser("scan", help="Run one full pipeline scan")

    scan_parser.add_argument(

        "--bankroll", type=float, default=10_000.0,

        help="Bankroll in USD for Kelly sizing (default: 10000)",

    )

    scan_parser.add_argument(
        "--deep-scan", action="store_true", help="Perform exhaustive brute-force event scan",
    )

    scan_parser.add_argument(

        "-v", "--verbose", action="store_true", help="Enable debug logging",

    )



    # -- diagnose ------------------------------------------------------

    diag_parser = subparsers.add_parser(

        "diagnose", help="Run scan with full diagnostics - see what's happening",

    )

    diag_parser.add_argument(

        "--bankroll", type=float, default=10_000.0,

    )

    diag_parser.add_argument(
        "--deep-scan", action="store_true", help="Perform exhaustive brute-force event scan",
    )

    diag_parser.add_argument(

        "-v", "--verbose", action="store_true", help="Enable debug logging",

    )

    diag_parser.add_argument(

        "--json", dest="output_json", action="store_true",

        help="Output diagnostics as JSON",

    )



    # -- calibrate -----------------------------------------------------

    cal_parser = subparsers.add_parser(

        "calibrate", help="Fetch historical data and build station calibration",

    )

    cal_parser.add_argument(
        "station", type=str,
        help="ICAO station ID to calibrate (e.g. NZWN, KORD)",
    )
    cal_parser.add_argument(
        "--days", type=int, default=1825,
        help="Number of historical days to fetch (default: 1825 = 5 years)",
    )
    cal_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging",
    )



    # -- run (scheduler) -----------------------------------------------

    run_parser = subparsers.add_parser(

        "run", help="Start the paper-trading scheduler (scan + log loop)",

    )

    run_parser.add_argument(

        "--bankroll", type=float, default=10_000.0,

        help="Bankroll in USD for Kelly sizing (default: 10000)",

    )

    run_parser.add_argument(
        "--interval", type=float, default=30.0,
        help="Minutes between scans (default: 30)",
    )
    run_parser.add_argument(
        "--duration", type=float, default=None,
        help="Run for a specific duration in hours, then exit",
    )

    run_parser.add_argument(

        "--once", action="store_true",

        help="Run a single scan-and-log cycle then exit",

    )

    run_parser.add_argument(
        "--deep-scan", action="store_true", help="Perform exhaustive brute-force event scan",
    )

    run_parser.add_argument(

        "-v", "--verbose", action="store_true", help="Enable debug logging",

    )



    # -- resolve -------------------------------------------------------

    resolve_parser = subparsers.add_parser(

        "resolve", help="Check for resolved markets and score predictions",

    )

    resolve_parser.add_argument(

        "-v", "--verbose", action="store_true", help="Enable debug logging",

    )



    # -- dashboard -----------------------------------------------------

    dash_parser = subparsers.add_parser(

        "dashboard", help="Launch the Streamlit paper-trading dashboard",

    )

    dash_parser.add_argument(

        "--port", type=int, default=8501,

        help="Port for Streamlit (default: 8501)",

    )

    # -- calibrate-all ------------------------------------------------
    cal_all_parser = subparsers.add_parser(
        "calibrate-all", help="Run calibration for all supported stations",
    )
    cal_all_parser.add_argument(
        "--days", type=int, default=1825,
        help="Number of days of history to use (default: 1825 = 5 years)",
    )
    cal_all_parser.add_argument(
        "--force", action="store_true",
        help="Re-run calibration for all stations even if already in DB",
    )
    cal_all_parser.add_argument(
        "--from-cache", action="store_true",
        help="Skip API calls: read existing disk cache files and write them to DB",
    )

    # -- backtest -----------------------------------------------------
    bt_parser = subparsers.add_parser(
        "backtest", help="Replay resolved markets and evaluate the strategy",
    )
    bt_parser.add_argument(
        "--start", type=str, required=True,
        help="Start date (inclusive) YYYY-MM-DD",
    )
    bt_parser.add_argument(
        "--end", type=str, required=True,
        help="End date (inclusive) YYYY-MM-DD",
    )
    bt_parser.add_argument(
        "--bankroll", type=float, default=10_000.0,
        help="Bankroll for Kelly sizing (default: 10000)",
    )
    bt_parser.add_argument(
        "--model", type=str, default="ecmwf_ifs",
        help="Forecast model to replay (default: ecmwf_ifs)",
    )
    bt_parser.add_argument(
        "--ensemble", action="store_true",
        help="Use multi-model ensemble instead of single model",
    )
    bt_parser.add_argument(
        "--report", type=str, default=None,
        help="Path to CSV file for detailed report export",
    )
    bt_parser.add_argument(
        "--mode", type=str, default="brier_only",
        choices=["brier_only", "full_sim"],
        help="Backtest mode: brier_only (forecast skill only) or full_sim (P&L simulation)",
    )
    bt_parser.add_argument(
        "--source", type=str, default="clob",
        choices=["gamma", "pmxt", "clob"],
        help=(
            "Market data source: "
            "clob (default — real pre-resolution prices from Polymarket CLOB prices-history, "
            "unlimited history), "
            "pmxt (archive.pmxt.dev Parquet — richer orderbook data, ~Feb 2026+), "
            "gamma (Gamma API post-resolution prices — only valid with --mode brier_only)"
        ),
    )
    bt_parser.add_argument(
        "--walk-forward", action="store_true",
        help="Enable true out-of-sample station calibration testing",
    )
    bt_parser.add_argument(
        "--latency-seconds", type=int, default=60,
        help="Simulated delay between model release and bot execution (default: 60)",
    )
    bt_parser.add_argument(
        "--fidelity", type=str, default="snapshot",
        choices=["snapshot", "tick"],
        help="Backtest fidelity: snapshot (Gamma) or tick (PMXT/Local)",
    )
    bt_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging",
    )

    # -- inspect ------------------------------------------------------
    inspect_parser = subparsers.add_parser(
        "inspect", help="Check historical forecast vs observation for a specific date"
    )
    inspect_parser.add_argument("station", type=str, help="Station ID (e.g. KLGA)")
    inspect_parser.add_argument("date", type=str, help="Date (YYYY-MM-DD) or Start Date if --end is passed")
    inspect_parser.add_argument("--end", type=str, help="Optional end date (YYYY-MM-DD) for range inspection")
    group = inspect_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--over", type=float, help="Check probability of temp over X")
    group.add_argument("--under", type=float, help="Check probability of temp under X")
    inspect_parser.add_argument(
        "--metric", type=str, default="tmax", choices=["tmax", "tmin"],
        help="Metric to inspect (default: tmax)"
    )
    inspect_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    # -- probe-pmxt ---------------------------------------------------
    probe_parser = subparsers.add_parser(
        "probe-pmxt",
        help="Test pmxt archive connectivity and inspect Parquet schema",
    )
    probe_parser.add_argument(
        "--date", type=str, default=None,
        help="Sample date to probe (YYYY-MM-DD, default: 2026-03-03)",
    )
    probe_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging",
    )


    # -- live-paper ---------------------------------------------------
    lp_parser = subparsers.add_parser(
        "live-paper", help="Start real-time paper trading using WebSockets"
    )
    lp_parser.add_argument(
        "--bankroll", type=float, default=10_000.0,
        help="Bankroll in USD for Kelly sizing (default: 10000)",
    )
    lp_parser.add_argument(
        "--interval", type=float, default=10.0,
        help="Seconds between evaluation cycles (default: 10)",
    )
    lp_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging",
    )

    args = parser.parse_args()




    if args.command is None:

        parser.print_help()

        sys.exit(0)



    # Logging

    level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO

    logging.basicConfig(

        level=level,

        format="%(asctime)s  %(name)-24s  %(levelname)-7s  %(message)s",

        datefmt="%H:%M:%S",

    )



    if args.command == "scan":

        _run_scan(args)

    elif args.command == "diagnose":

        _run_diagnose(args)

    elif args.command == "calibrate":
        _run_calibrate(args)
    elif args.command == "calibrate-all":
        _run_calibrate_all(args)
    elif args.command == "run":

        _run_scheduler(args)

    elif args.command == "resolve":

        _run_resolve(args)

    elif args.command == "dashboard":

        _run_dashboard(args)

    elif args.command == "backtest":
        _run_backtest(args)
    elif args.command == "inspect":
        _run_inspect(args)
    elif args.command == "live-paper":
        _run_live_paper(args)
    elif args.command == "probe-pmxt":
        _run_probe_pmxt(args)






# =========================================================================

# Scan

# =========================================================================



def _run_scan(args: argparse.Namespace) -> None:

    signals, _ = asyncio.run(run_scan(
        bankroll=args.bankroll,
        deep_scan=args.deep_scan,
    ))



    if not signals:

        print("\n  No tradeable signals found.\n")

        return



    _print_signals(signals, args.bankroll)





# =========================================================================

# Diagnose

# =========================================================================



def _run_diagnose(args: argparse.Namespace) -> None:

    signals, diag = asyncio.run(
        run_scan(
            bankroll=args.bankroll,
            diagnostics=True,
            deep_scan=args.deep_scan,
        )
    )



    if args.output_json and diag:

        import dataclasses

        print(json.dumps(dataclasses.asdict(diag), indent=2, default=str))

        return



    print()

    print("=" * 80)

    print("  WeatherEdge Diagnostic Report")

    print("=" * 80)

    print()



    if diag:

        print(f"  Weather markets found:     {diag.weather_markets_found}")

        print(f"  Matched to stations:       {diag.markets_with_station}")

        print(f"  Unmatched (no station):    {diag.markets_without_station}")

        print(f"  Unique stations:           {diag.stations_matched}")

        print(f"  Forecasts fetched:         {diag.forecasts_fetched}")

        print(f"  Markets processed:         {diag.markets_processed}")

        print(f"  Blocked by regime flags:   {diag.markets_blocked_by_regime}")

        print(f"  Bins evaluated:            {diag.bins_evaluated}")

        print(f"  Signals above threshold:   {diag.signals_above_threshold}")

        print()



        if diag.errors:

            print("  ⚠ Errors:")

            for e in diag.errors:

                print(f"    • {e}")

            print()



        # Show market details

        if diag.market_details:

            print("  Market Details:")

            print("  " + "-" * 76)

            for md in diag.market_details[:20]:

                q = md.get("question", "")[:60]

                sid = md.get("station_id", "???")

                skip = md.get("skip_reason", "")

                flags = md.get("regime_flags", [])

                model_p = md.get("model_probs", [])

                mkt_p = md.get("market_probs", [])

                sigs = md.get("signals_found", 0)

                cal = "✓" if md.get("calibration_applied") else "✗"



                print(f"  [{sid}] {q}")

                if skip:

                    print(f"    -> Skipped: {skip}")

                else:

                    print(f"    Cal: {cal}  Signals: {sigs}  Flags: {flags or 'none'}")

                    if model_p and mkt_p:

                        edges = [round(m - k, 3) for m, k in zip(model_p, mkt_p)]

                        print(f"    Model%:  {model_p}")

                        print(f"    Market%: {mkt_p}")

                        print(f"    Edges:   {edges}")

                print()



    if signals:

        _print_signals(signals, args.bankroll)

    else:

        print("  No tradeable signals above threshold.\n")





# =========================================================================

# Calibrate

# =========================================================================



def _run_calibrate(args: argparse.Namespace) -> None:
    from weatheredge.calibration import run_calibration
    from weatheredge.db import get_session, init_db, upsert_calibration

    async def _do():
        await init_db()
        bias, metrics = await run_calibration(args.station, days=args.days)

        if "error" in metrics:
            print(f"\n  ✗ Calibration failed: {metrics['error']}\n")
            return

        print()
        print("=" * 60)
        print(f"  Station Calibration Report: {args.station}")
        print("=" * 60)
        print(f"  Configuration:")
        print(f"    Lookback:       {args.days} days")
        print(f"    Train Sample:   {metrics['train_size']} paired days")
        print(f"    Test Sample:    {metrics['test_size']} paired days")
        print()
        
        print(f"  OOS Validation (Holdout Set):")
        print(f"    Raw MSE:        {metrics['raw_oos_mse']:.4f}")
        print(f"    Calibrated MSE: {metrics['cal_oos_mse']:.4f}")
        
        imprvt = metrics['oos_improvement_pct']
        color = "\033[92m" if imprvt > 0 else "\033[91m"
        print(f"    Improvement:   {color}{imprvt:+.1f}%\033[0m")
        print()

        print("  Seasonal Tmax Biases (Obs - Forecast):")
        print("    Season      Bias      p-val     N      Status")
        print("    " + "-" * 50)
        
        for s in ["summer", "winter", "spring", "autumn"]:
            b_val = getattr(bias, f"tmax_bias_{s}")
            p_val = bias.tmax_p_values.get(s, 1.0)
            count = bias.seasonal_counts.get(s, 0)
            
            # Status indicators
            sig = "✓ significant" if p_val < 0.05 else "✗ noisy"
            if count < 50:
                sig += " (small sample)"
                
            print(f"    {s:<11} {b_val:>+6.2f}C    {p_val:>5.3f}    {count:>3}    {sig}")

        print()
        print(f"  Overall Tmax RMSE: {bias.tmax_rmse:.2f}C")
        print("=" * 60)
        print()

        # Prompt to save
        if imprvt <= 0:
            print("  ⚠️ WARNING: Calibration did not improve performance on holdout set.")
        
        confirm = input(f"  Save these calibrations for {args.station} to database? [y/N]: ").lower()
        if confirm == 'y':
            async with get_session() as session:
                await upsert_calibration(session, bias)
            print(f"\n  ✓ Calibrations saved for {args.station}.")
        else:
            print("\n  Discarded calibrations.")

    asyncio.run(_do())


def _run_calibrate_all(args: argparse.Namespace) -> None:
    from weatheredge.calibrate_all import run_all_calibrations, sync_cache_to_db

    if getattr(args, "from_cache", False):
        results = asyncio.run(sync_cache_to_db())
    else:
        results = asyncio.run(run_all_calibrations(days=args.days, force=args.force))

    if not results:
        print("\n  ✗ No stations processed.")
        sys.exit(1)


# =========================================================================

# Shared printing

# =========================================================================



def _print_signals(signals: list, bankroll: float) -> None:

    print()

    print("=" * 100)

    print(f"  WeatherEdge Scan - {len(signals)} signal(s) above threshold")

    print("=" * 100)

    print()



    header = (

        f"{'#':<3} "

        f"{'Station':<7} "

        f"{'Bin':<16} "

        f"{'Model%':>7} "

        f"{'Market%':>8} "

        f"{'Edge%':>7} "

        f"{'Kelly':>7} "

        f"{'Bet $':>8} "

        f"{'Question':<40}"

    )

    print(header)

    print("-" * 100)



    for i, s in enumerate(signals, 1):

        q = s.question[:38] + ".." if len(s.question) > 40 else s.question

        print(

            f"{i:<3} "

            f"{s.station_id:<7} "

            f"{s.bin_label:<16} "

            f"{s.model_prob:>6.1%} "

            f"{s.market_prob:>7.1%} "

            f"{s.net_edge:>6.1%} "

            f"{s.kelly_fraction:>6.3f} "

            f"{s.recommended_bet_usd:>7.2f} "

            f"{q:<40}"

        )



    print()

    print(f"  Bankroll: ${bankroll:,.0f}")

    print(f"  Total recommended: ${sum(s.recommended_bet_usd for s in signals):,.2f}")

    print()





# =========================================================================

# Run (scheduler)

# =========================================================================



def _run_scheduler(args: argparse.Namespace) -> None:

    from weatheredge.scheduler import run_loop



    print()

    print("=" * 60)

    print("  WeatherEdge Paper Trading Scheduler")

    print("=" * 60)

    print(f"  Bankroll:  ${args.bankroll:,.0f}")

    print(f"  Interval:  {args.interval} minutes")

    print(f"  Mode:      {'single cycle' if args.once else 'continuous'}")

    print("=" * 60)

    print()



    asyncio.run(run_loop(
        interval_minutes=args.interval,
        bankroll=args.bankroll,
        once=args.once,
        duration_hours=args.duration,
    ))





# =========================================================================

# Resolve

# =========================================================================



def _run_resolve(args: argparse.Namespace) -> None:

    from weatheredge.db import init_db

    from weatheredge.resolver import check_resolutions



    async def _do():

        await init_db()

        resolved = await check_resolutions()

        print(f"\n  Resolved {resolved} signal(s).\n")



    asyncio.run(_do())





# =========================================================================

# Dashboard

# =========================================================================



def _run_dashboard(args: argparse.Namespace) -> None:

    import subprocess

    import sys

    from pathlib import Path



    dashboard_path = Path(__file__).parent / "dashboard.py"

    port = str(args.port)



    print(f"\n  Launching Streamlit dashboard on port {port}...")

    print(f"  Open http://localhost:{port} in your browser.\n")



    subprocess.run([

        sys.executable, "-m", "streamlit", "run",

        str(dashboard_path),

        "--server.port", port,

        "--server.headless", "true",

    ])





# =========================================================================

# Backtest

# =========================================================================



def _run_backtest(args: argparse.Namespace) -> None:

    from weatheredge.backtester import BacktestConfig, run_backtest



    print()

    print("=" * 60)

    print("  WeatherEdge Backtester")

    print("=" * 60)

    print(f"  Window:    {args.start} -> {args.end}")

    print(f"  Bankroll:  ${args.bankroll:,.0f}")

    print(f"  Model:     {'ensemble' if args.ensemble else args.model}")

    print(f"  Mode:      {args.mode}")

    print(f"  Source:    {args.source}")

    print("=" * 60)

    print()



    async def _do():
        from weatheredge.db import init_db, get_calibrations, get_session
        await init_db()
        async with get_session() as session:
            cals = await get_calibrations(session)

        cfg: BacktestConfig = {
            "start_date": args.start,
            "end_date": args.end,
            "bankroll": args.bankroll,
            "model": args.model,
            "use_ensemble": args.ensemble,
            "mode": args.mode,
            "data_source": args.source,
            "walk_forward": args.walk_forward,
            "latency_seconds": args.latency_seconds,
            "fidelity": args.fidelity,
            "calibrations": cals,
        }

        bt_run = await run_backtest(cfg)

        print()
        print("-" * 60)
        print(f"  Run ID:       {bt_run.id}")
        print(f"  Markets:      {bt_run.markets_count}")
        print(f"  Signals:      {bt_run.signals_count}")
        print(f"  Total P&L:    ${bt_run.total_pnl or 0:,.2f}")
        print(f"  Avg Brier:    {bt_run.avg_brier or 0:.4f}")
        print(f"  Avg RPS:      {bt_run.avg_rps or 0:.4f}  (ordinal-aware; lower = better)")
        print(f"  Avg LogScore: {bt_run.avg_log_score or 0:.3f} bits  (Kelly-linked; lower = better)")
        print("-" * 60)
        print()

        if args.report:
            _export_backtest_report(bt_run.id, args.report)

    asyncio.run(_do())





def _export_backtest_report(run_id: int, path: str) -> None:

    """Export backtest results to CSV."""

    import sqlite3

    import pandas as pd

    from weatheredge.config import DATABASE_URL



    # Extract file path from SQLAlchemy URL

    db_path = DATABASE_URL

    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):

        if db_path.startswith(prefix):

            db_path = db_path[len(prefix):]

            break



    conn = sqlite3.connect(db_path)

    df = pd.read_sql_query("""

        SELECT

            s.created_at,

            m.station_id,

            m.question,

            s.bin_index,

            s.model_prob,

            s.market_prob,

            s.edge,

            s.kelly_fraction,

            s.bet_size_usd,

            r.won,

            r.pnl_usd,

            r.resolved_bin

        FROM signals s

        JOIN markets m ON s.market_id = m.id

        LEFT JOIN results r ON r.signal_id = s.id

        WHERE s.backtest_run_id = ?

        ORDER BY s.created_at

    """, conn, params=(run_id,))

    conn.close()

    df.to_csv(path, index=False)

    print(f"  Report exported to: {path}")





def _run_inspect(args: argparse.Namespace) -> None:
    from weatheredge.backtest_fetchers import fetch_historical_forecast_ensemble
    from weatheredge.mesonet import fetch_daily_observations
    from scipy.stats import norm
    from datetime import date as dt_date, timedelta
    from weatheredge.db import get_session, get_calibrations
    from weatheredge.calibration import apply_calibration, apply_calibration_std

    async def _do():
        start_date = dt_date.fromisoformat(args.date)
        end_date = dt_date.fromisoformat(args.end) if args.end else start_date
        
        if end_date < start_date:
            print("Error: End date must be on or after start date.")
            return
            
        station_id = args.station.upper()
        
        station = get_station(station_id)
        if not station:
            print(f"Error: Station {station_id} not found in stations.py")
            return

        print(f"\nInspecting {station_id} from {start_date} to {end_date}...")
        
        async with get_session() as session:
            cals = await get_calibrations(session)
            calibration = cals.get(station_id)

        # Pre-fetch all observations for the range in one go to save API calls
        all_obs = await fetch_daily_observations(station_id, start_date, end_date)
        obs_by_date = {obs.date: obs for obs in all_obs}

        hits = 0
        misses = 0
        skips = 0
        bets_won = 0
        bets_lost = 0
        bets_passed = 0

        current_date = start_date
        while current_date <= end_date:
            print("-" * 60)
            print(f"Date: {current_date}")
            
            # 1. Fetch Forecast
            forecast = await fetch_historical_forecast_ensemble(
                station.latitude, station.longitude, current_date, timezone=station.timezone
            )
            
            # 2. Match Observation
            observation = obs_by_date.get(current_date)

            if not forecast:
                print("  ✗ No historical forecast found for this date.")
                skips += 1
                current_date += timedelta(days=1)
                continue

            # 3. Probability Calculation (with Calibration)
            raw_mean = forecast.tmax_mean if args.metric == "tmax" else (forecast.tmin_mean or 0)
            raw_std = forecast.tmax_std if args.metric == "tmax" else (forecast.tmin_std or 2.0)
            
            if calibration:
                mean = apply_calibration(raw_mean, calibration, current_date, metric=args.metric)
                std = apply_calibration_std(raw_std, calibration)
                cal_status = "Calibrated"
            else:
                mean = raw_mean
                std = raw_std
                cal_status = "Raw"
            
            dist = norm(loc=mean, scale=std)
            
            # Unit Handling for Contract threshold
            from weatheredge.polymarket import fahrenheit_to_celsius
            display_unit = station.preferred_unit
            if args.over is not None:
                raw_threshold = args.over
                threshold = fahrenheit_to_celsius(raw_threshold) if display_unit == "F" else raw_threshold
                prob = 1.0 - dist.cdf(threshold)
                op = ">"
            else:
                raw_threshold = args.under
                threshold = fahrenheit_to_celsius(raw_threshold) if display_unit == "F" else raw_threshold
                prob = dist.cdf(threshold)
                op = "<"

            actual_val = observation.tmax_c if (observation and args.metric == "tmax") else observation.tmin_c if observation else None
            
            print(f"{'Forecast (Ensemble)':<20} {mean:>6.1f}°C  (std={std:.1f}) [{cal_status}]")
            if actual_val is not None:
                print(f"{'Actual (Observation)':<20} {actual_val:>6.1f}°C")
            else:
                print(f"{'Actual (Observation)':<20} {'N/A':>7}")
            
            print(f"Contract: Is {args.metric} {op} {raw_threshold}°{display_unit} ({threshold:.1f}°C)?")
            
            color = "\033[94m" # blue
            print(f"Model Probability: {color}{prob:>6.1%}\033[0m")
            
            if actual_val is not None:
                hit = (actual_val > threshold) if args.over is not None else (actual_val < threshold)
                res_color = "\033[92m" if hit else "\033[91m"
                print(f"Actual Outcome:    {res_color}{'HIT' if hit else 'MISS'}\033[0m")
                if hit:
                    hits += 1
                else:
                    misses += 1
                    
                if prob > 0.5:
                    bet_won = hit
                    bet_str = "YES"
                elif prob < 0.5:
                    bet_won = not hit
                    bet_str = "NO"
                else:
                    bet_won = None
                    bet_str = "PASS"
                    
                if bet_won is True:
                    print(f"Simulated Bet:     {bet_str} \033[92m(WON)\033[0m")
                    bets_won += 1
                elif bet_won is False:
                    print(f"Simulated Bet:     {bet_str} \033[91m(LOST)\033[0m")
                    bets_lost += 1
                else:
                    print(f"Simulated Bet:     {bet_str}")
                    bets_passed += 1
                    
            else:
                print("Actual Outcome:    UNKNOWN (No observation data)")
                skips += 1
                
            current_date += timedelta(days=1)

        print("-" * 60)
        
        # Summary
        if start_date != end_date:
            print(f"\nRANGE SUMMARY ({start_date} to {end_date}):")
            print(f"Total Days Evaluated: {hits + misses}")
            if skips > 0:
                print(f"Missing Data (Skipped): {skips}")
            if (hits + misses) > 0:
                win_rate = hits / (hits + misses)
                print(f"Occurrences (Hits): {hits} / {hits + misses} ({win_rate:.1%})")
                
                bet_total = bets_won + bets_lost
                if bet_total > 0:
                    bet_win_rate = bets_won / bet_total
                    print(f"Simulated Bets Won: {bets_won} / {bet_total} ({bet_win_rate:.1%})")
                if bets_passed > 0:
                    print(f"Simulated Bets Passed: {bets_passed}")
            print("-" * 60 + "\n")
        else:
            print("")

    asyncio.run(_do())



# =========================================================================
# Probe pmxt
# =========================================================================


def _run_probe_pmxt(args: argparse.Namespace) -> None:
    """Test pmxt archive connectivity and inspect Parquet schema."""
    from datetime import date as dt_date
    from weatheredge.pmxt_fetcher import probe_schema, _download_parquet, build_date_urls

    sample_date = (
        dt_date.fromisoformat(args.date) if args.date
        else dt_date(2026, 3, 3)
    )

    print()
    print("=" * 60)
    print("  pmxt Archive Probe")
    print("=" * 60)
    print(f"  Sample date: {sample_date}")
    print()

    print("  Fetching schema...")
    schema = probe_schema(sample_date)

    if not schema:
        print("  ✗ Failed to connect to pmxt archive.")
        print("    The service may be down — try again later.")
        return

    print(f"  ✓ Connected! Schema has {len(schema)} columns:")
    print()
    for col, dtype in schema.items():
        print(f"    {col:<30} {dtype}")
    print()

    # Show a sample data row
    try:
        url = build_date_urls(sample_date, sample_date)[0][1]
        df = _download_parquet(url)
        total_rows = len(df)
        unique_markets = df["market_id"].n_unique() if "market_id" in df.columns else "?"
        update_types = df["update_type"].unique().to_list() if "update_type" in df.columns else []

        print(f"  Total rows:      {total_rows:,}")
        print(f"  Unique markets:  {unique_markets}")
        print(f"  Update types:    {update_types}")
        print()

        # Show a sample data JSON
        if "data" in df.columns:
            sample_row = df.head(1).row(0, named=True)
            data_json = json.loads(sample_row["data"]) if isinstance(sample_row.get("data"), str) else {}
            print("  Sample data keys:", list(data_json.keys()))
            if "best_bid" in data_json:
                print(f"    best_bid: {data_json['best_bid']}")
                print(f"    best_ask: {data_json['best_ask']}")
    except Exception as e:
        print(f"  (Could not fetch sample data: {e})")

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()


