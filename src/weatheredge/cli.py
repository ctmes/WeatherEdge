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

    python -m weatheredge dashboard         # launch Streamlit dashboard

"""



from __future__ import annotations



import argparse

import asyncio

import json

import logging

import sys



from weatheredge.pipeline import run_scan





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

        "--days", type=int, default=90,

        help="Number of historical days to fetch (default: 90)",

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

        "--once", action="store_true",

        help="Run a single scan-and-log cycle then exit",

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

    elif args.command == "run":

        _run_scheduler(args)

    elif args.command == "resolve":

        _run_resolve(args)

    elif args.command == "dashboard":

        _run_dashboard(args)

    elif args.command == "backtest":

        _run_backtest(args)





# =========================================================================

# Scan

# =========================================================================



def _run_scan(args: argparse.Namespace) -> None:

    signals, _ = asyncio.run(run_scan(bankroll=args.bankroll))



    if not signals:

        print("\n  No tradeable signals found.\n")

        return



    _print_signals(signals, args.bankroll)





# =========================================================================

# Diagnose

# =========================================================================



def _run_diagnose(args: argparse.Namespace) -> None:

    signals, diag = asyncio.run(

        run_scan(bankroll=args.bankroll, diagnostics=True)

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

    from datetime import date, timedelta



    from weatheredge.calibration import (

        StationBias,

        build_calibration,

        pair_observations_with_forecasts,

    )

    from weatheredge.mesonet import fetch_daily_observations

    from weatheredge.stations import get_station



    station = get_station(args.station)

    if station is None:

        print(f"\n  ✗ Unknown station: {args.station}\n")

        sys.exit(1)



    end = date.today()

    start = end - timedelta(days=args.days)



    print(f"\n  Fetching {args.days} days of observations for {station.id} ({station.name})...")

    observations = asyncio.run(fetch_daily_observations(station.id, start, end))

    print(f"  Retrieved {len(observations)} daily observations.")



    if not observations:

        print("  ✗ No observations found. Check station ID.\n")

        sys.exit(1)



    # Show basic stats

    tmax_vals = [o.tmax_c for o in observations if o.tmax_c is not None]

    tmin_vals = [o.tmin_c for o in observations if o.tmin_c is not None]



    print()

    print(f"  Station: {station.id} - {station.name}")

    print(f"  Period:  {start} to {end}")

    print(f"  Days with tmax: {len(tmax_vals)}")

    print(f"  Days with tmin: {len(tmin_vals)}")



    if tmax_vals:

        import numpy as np

        print(f"  Tmax range: {min(tmax_vals):.1f} C to {max(tmax_vals):.1f} C "

              f"(mean: {np.mean(tmax_vals):.1f} C)")

    if tmin_vals:

        import numpy as np

        print(f"  Tmin range: {min(tmin_vals):.1f} C to {max(tmin_vals):.1f} C "

              f"(mean: {np.mean(tmin_vals):.1f} C)")



    print(f"\n  ℹ Calibration pairs require stored forecast data.")

    print(f"    Run the pipeline with logging enabled to build forecast history,")

    print(f"    then re-run calibrate to compute bias corrections.\n")





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

    print("=" * 60)

    print()



    cfg: BacktestConfig = {

        "start_date": args.start,

        "end_date": args.end,

        "bankroll": args.bankroll,

        "model": args.model,

        "use_ensemble": args.ensemble,

        "mode": args.mode,

    }



    bt_run = asyncio.run(run_backtest(cfg))



    print()

    print("-" * 60)

    print(f"  Run ID:       {bt_run.id}")

    print(f"  Markets:      {bt_run.markets_count}")

    print(f"  Signals:      {bt_run.signals_count}")

    print(f"  Total P&L:    ${bt_run.total_pnl or 0:,.2f}")

    print(f"  Avg Brier:    {bt_run.avg_brier or 0:.4f}")

    print("-" * 60)

    print()



    if args.report:

        _export_backtest_report(bt_run.id, args.report)





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

    df = pd.read_sql_query(f"""

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

        WHERE s.backtest_run_id = {run_id}

        ORDER BY s.created_at

    """, conn)

    conn.close()

    df.to_csv(path, index=False)

    print(f"  Report exported to: {path}")





if __name__ == "__main__":

    main()

