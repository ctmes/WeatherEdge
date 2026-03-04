"""
WeatherEdge —  Dashboard (Streamlit)

Launch:
    python -m weatheredge dashboard
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from weatheredge import analytics
from weatheredge.config import DATABASE_URL, TRADING

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMEFRAMES = {
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "3 days": timedelta(days=3),
    "1 week": timedelta(weeks=1),
    "2 weeks": timedelta(weeks=2),
    "4 weeks": timedelta(weeks=4),
    "3 months": timedelta(days=90),
    "6 months": timedelta(days=180),
    "All Time": None,
}


def _get_db_path() -> str:
    raw = DATABASE_URL
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return "weatheredge.db"


def _query(sql: str, params: tuple = ()) -> pd.DataFrame:
    path = _get_db_path()
    if not Path(path).exists():
        return pd.DataFrame()
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def _cutoff(tf_key: str) -> str | None:
    delta = TIMEFRAMES.get(tf_key)
    if delta is None:
        return None
    return (datetime.utcnow() - delta).isoformat()


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid #0f3460;
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    }
    [data-testid="stMetricLabel"] {
        color: #7f8c8d;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }
    [data-testid="stMetricValue"] {
        font-weight: 700;
        font-size: 1.6rem;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        padding: 10px 20px;
        font-weight: 600;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid #0f3460;
        border-radius: 8px;
    }
    .terminal-output {
        background: #0d1117;
        color: #c9d1d9;
        font-family: 'SF Mono', 'Fira Code', 'Courier New', monospace;
        font-size: 0.82rem;
        padding: 16px;
        border-radius: 8px;
        border: 1px solid #30363d;
        max-height: 500px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-wrap: break-word;
    }
    .status-running {
        background: linear-gradient(135deg, #0d1f0d 0%, #1a2e1a 100%);
        border: 1px solid #238636;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    .status-done {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid #1f6feb;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    .status-error {
        background: linear-gradient(135deg, #2d1117 0%, #3d1a1a 100%);
        border: 1px solid #da3633;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
</style>
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="WeatherEdge",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(_CSS, unsafe_allow_html=True)

    db_path = _get_db_path()
    if not Path(db_path).exists():
        st.error(
            "⚠️ Database not found. Run `python -m weatheredge run --once` "
            "to initialise and collect first data."
        )
        return

    # ── Sidebar ───────────────────────────────────────────────────
    with st.sidebar:
        st.image("https://img.icons8.com/fluency/96/lightning-bolt.png", width=48)
        st.title("WeatherEdge")
        st.caption("Dashboard")
        st.divider()

        tf = st.selectbox(
            "📅 Timeframe",
            list(TIMEFRAMES.keys()),
            index=2,  # default 24h
            help="Filter all metrics and tables to this rolling window.",
        )
        cutoff = _cutoff(tf)

        st.divider()
        st.markdown("**Database Management**")
        if st.checkbox("Confirm: Wipe all history?"):
            if st.button("🚨 Clear All History", type="secondary", use_container_width=True):
                try:
                    conn = sqlite3.connect(_get_db_path())
                    c = conn.cursor()
                    # Delete performance tracking but keep station and market meta if possible?
                    # User said "all metrics basically", so purge:
                    c.execute("DELETE FROM results")
                    c.execute("DELETE FROM signals")
                    c.execute("DELETE FROM brier_scores")
                    c.execute("DELETE FROM backtest_runs")
                    conn.commit()
                    conn.close()
                    st.success("History wiped!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to clear history: {e}")

        st.divider()
        st.markdown("**Trading Config**")
        st.code(
            f"Min Edge:   {TRADING.min_edge_threshold:.0%}\n"
            f"Slippage:   {TRADING.assumed_slippage:.0%}\n"
            f"Fee:        {TRADING.polymarket_fee:.0%}\n"
            f"Kelly Frac: {TRADING.kelly_fraction:.0%}\n"
            f"Max Bet:    {TRADING.kelly_max_bet:.0%}",
            language=None,
        )
        st.divider()
        st.caption(f"DB: `{Path(db_path).name}`")

    # ── Title ─────────────────────────────────────────────────────
    st.markdown("# ⚡ WeatherEdge Trading Bot")
    st.caption(f"Showing data for **{tf}** window  ·  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # ── Tabs ──────────────────────────────────────────────────────
    tab_perf, tab_active, tab_risk, tab_scanner, tab_history, tab_cmd = st.tabs([
        "📈 Performance",
        "💼 Active Bets",
        "🛡️ Risk Guardrails",
        "🔎 Market Scanner",
        "🕰️ Market History",
        "🖥️ Command Center",
    ])

    # ==================================================================
    # TAB 1: PERFORMANCE
    # ==================================================================
    with tab_perf:
        _render_performance(cutoff, tf)

    # ==================================================================
    # TAB 2: ACTIVE BETS
    # ==================================================================
    with tab_active:
        _render_active_bets()

    # ==================================================================
    # TAB 3: RISK GUARDRAILS
    # ==================================================================
    with tab_risk:
        _render_risk_guardrails()

    # ==================================================================
    # TAB 4: MARKET SCANNER
    # ==================================================================
    with tab_scanner:
        _render_scanner()

    # ==================================================================
    # TAB 5: MARKET HISTORY
    # ==================================================================
    with tab_history:
        _render_history(cutoff, tf)

    # ==================================================================
    # TAB 6: COMMAND CENTER
    # ==================================================================
    with tab_cmd:
        _render_command_center()


# ======================================================================
# Tab Renderers
# ======================================================================


def _render_performance(cutoff: str | None, tf: str) -> None:
    """Performance metrics, equity curve, Brier, reliability diagram."""

    time_filter = f"AND r.resolved_at >= '{cutoff}'" if cutoff else ""
    signal_filter = f"AND s.created_at >= '{cutoff}'" if cutoff else ""

    # ── Core metrics row ──────────────────────────────────────────
    results_df = _query(f"""
        SELECT
            COUNT(*) as n,
            SUM(CASE WHEN r.won THEN 1 ELSE 0 END) as wins,
            SUM(r.pnl_usd) as total_pnl,
            SUM(CASE WHEN r.pnl_usd > 0 THEN r.pnl_usd ELSE 0 END) as gross_profit,
            SUM(CASE WHEN r.pnl_usd < 0 THEN ABS(r.pnl_usd) ELSE 0 END) as gross_loss,
            AVG(r.pnl_usd) as avg_pnl,
            SUM(s.bet_size_usd) as total_volume
        FROM results r
        JOIN signals s ON r.signal_id = s.id
        WHERE 1=1 {time_filter}
    """)

    brier_df = _query(f"""
        SELECT AVG(brier_score) as avg_brier, AVG(baseline_brier) as avg_baseline
        FROM brier_scores
        WHERE 1=1 {"AND calculated_at >= '" + cutoff + "'" if cutoff else ""}
    """)

    bt_stats = _query("SELECT sharpe_ratio, max_drawdown FROM backtest_runs ORDER BY finished_at DESC LIMIT 1")

    n = int(results_df.iloc[0]["n"]) if len(results_df) and results_df.iloc[0]["n"] else 0
    wins = int(results_df.iloc[0]["wins"] or 0) if n else 0
    total_pnl = float(results_df.iloc[0]["total_pnl"] or 0) if n else 0
    gross_profit = float(results_df.iloc[0]["gross_profit"] or 0) if n else 0
    gross_loss = float(results_df.iloc[0]["gross_loss"] or 0) if n else 0
    total_volume = float(results_df.iloc[0]["total_volume"] or 0) if n else 0
    
    avg_brier = float(brier_df.iloc[0]["avg_brier"]) if len(brier_df) and brier_df.iloc[0]["avg_brier"] is not None else None
    avg_baseline = float(brier_df.iloc[0]["avg_baseline"]) if len(brier_df) and brier_df.iloc[0]["avg_baseline"] is not None else None
    bss = analytics.brier_skill_score(avg_brier, avg_baseline) if avg_brier is not None and avg_baseline is not None else None
    
    win_rate = wins / n if n > 0 else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0
    roi = (total_pnl / total_volume * 100) if total_volume > 0 else 0

    sharpe = None
    mdd = None
    if len(bt_stats) and not bt_stats.iloc[0].isna().all():
        sharpe = bt_stats.iloc[0]["sharpe_ratio"]
        mdd = bt_stats.iloc[0]["max_drawdown"]

    # Row 1
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total P&L", f"${total_pnl:,.2f}", delta=f"{'+' if total_pnl >= 0 else ''}{total_pnl:,.0f}")
    c2.metric("Win Rate", f"{win_rate:.1%}" if n else "—")
    c3.metric("Trades", f"{n}")
    c4.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞" if gross_profit else "—")
    c5.metric("ROI", f"{roi:.1f}%" if total_volume else "—")

    # Row 2
    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("Sharpe Ratio", f"{sharpe:.2f}" if sharpe is not None else "—")
    c7.metric("Max Drawdown", f"{mdd * 100:.1f}%" if mdd is not None else "—")
    c8.metric("Brier Skill Score", f"{bss:+.2%}" if bss is not None else "—", 
              help="BSS measures skill vs a naive climatological baseline. Positive is good.")
    c9.metric("Avg Brier", f"{avg_brier:.4f}" if avg_brier is not None else "—")
    c10.metric("Avg P&L/Trade", f"${total_pnl / n:,.2f}" if n else "—")

    st.divider()

    # ── Confidence Interval ──────────────────────────────────────
    if n > 0:
        lo, hi = analytics.win_rate_confidence_interval(wins, n)
        st.caption(f"Win Rate 95% CI: **[{lo:.1%} – {hi:.1%}]**  ·  n={n}")

    # ── Charts ────────────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("📈 Equity Curve")
        equity_df = _query(f"""
            SELECT r.resolved_at,
                   r.pnl_usd,
                   SUM(r.pnl_usd) OVER (ORDER BY r.resolved_at) as cumulative_pnl
            FROM results r
            JOIN signals s ON r.signal_id = s.id
            WHERE 1=1 {time_filter}
            ORDER BY r.resolved_at
        """)
        if len(equity_df) > 0:
            equity_df["resolved_at"] = pd.to_datetime(equity_df["resolved_at"])
            st.line_chart(equity_df.set_index("resolved_at")["cumulative_pnl"], use_container_width=True)
        else:
            st.info("No P&L data for this timeframe.")

    with col_right:
        st.subheader("🎯 Brier Score Trend")
        bs_df = _query(f"""
            SELECT calculated_at, brier_score
            FROM brier_scores
            WHERE 1=1 {"AND calculated_at >= '" + cutoff + "'" if cutoff else ""}
            ORDER BY calculated_at
        """)
        if len(bs_df) > 1:
            bs_df["calculated_at"] = pd.to_datetime(bs_df["calculated_at"])
            st.line_chart(bs_df.set_index("calculated_at")["brier_score"], use_container_width=True)
        else:
            st.info("Not enough data for Brier trend.")

    # ── Returns Distribution ─────────────────────────────────────
    col_left2, col_right2 = st.columns(2)

    with col_left2:
        st.subheader("📊 Returns Distribution")
        pnl_df = _query(f"""
            SELECT r.pnl_usd
            FROM results r
            JOIN signals s ON r.signal_id = s.id
            WHERE 1=1 {time_filter}
        """)
        if len(pnl_df) > 2:
            st.bar_chart(pnl_df["pnl_usd"].value_counts(bins=20).sort_index(), use_container_width=True)
        else:
            st.info("Need more trades for distribution.")

    with col_right2:
        st.subheader("🎯 Calibration Diagram")
        calib_df = _query(f"""
            SELECT s.model_prob, CASE WHEN r.won THEN 1 ELSE 0 END as won
            FROM signals s
            JOIN results r ON r.signal_id = s.id
            WHERE 1=1 {signal_filter}
        """)
        if len(calib_df) > 10:
            bin_data = analytics.reliability_diagram_data(
                calib_df["model_prob"].tolist(),
                calib_df["won"].tolist(),
                n_bins=10,
            )
            plot_df = pd.DataFrame(bin_data)
            st.bar_chart(plot_df.set_index("bin_center")[["actual_frequency", "predicted_mean"]], use_container_width=True)
            st.caption("Perfect calibration: bars should be equal height.")
        else:
            st.info("Need >10 resolved samples for reliability diagram.")

    # ── Station breakdown ────────────────────────────────────────
    st.divider()
    st.subheader("🏢 Performance by Station")
    station_df = _query(f"""
        SELECT
            m.station_id,
            COUNT(*) as trades,
            SUM(CASE WHEN r.won THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(r.pnl_usd), 2) as total_pnl,
            ROUND(AVG(r.pnl_usd), 2) as avg_pnl,
            ROUND(AVG(s.edge) * 100, 1) as avg_edge_pct
        FROM signals s
        JOIN markets m ON s.market_id = m.id
        JOIN results r ON r.signal_id = s.id
        WHERE m.station_id IS NOT NULL {time_filter}
        GROUP BY m.station_id
        ORDER BY total_pnl DESC
    """)
    if len(station_df):
        st.dataframe(station_df, use_container_width=True, hide_index=True)
    else:
        st.info("No station data for this timeframe.")

    # ── Backtest Runs ────────────────────────────────────────────
    st.divider()
    st.subheader("🧪 Backtest Runs")
    bt_df = _query("""
        SELECT
            id, started_at, markets_count, signals_count,
            ROUND(total_pnl, 2) as total_pnl,
            ROUND(avg_brier, 4) as avg_brier,
            ROUND(max_drawdown * 100, 1) as max_dd_pct,
            ROUND(sharpe_ratio, 2) as sharpe
        FROM backtest_runs
        ORDER BY id DESC
        LIMIT 10
    """)
    if len(bt_df):
        st.dataframe(bt_df, use_container_width=True, hide_index=True)
    else:
        st.info("No backtest runs found.")


def _render_active_bets() -> None:
    """Show signals that have not yet been resolved."""

    active_df = _query("""
        SELECT
            s.created_at,
            m.station_id,
            m.question,
            s.bin_index,
            ROUND(s.model_prob * 100, 1) as model_pct,
            ROUND(s.market_prob * 100, 1) as market_pct,
            ROUND(s.slippage * 100, 1) as slippage_pct,
            ROUND(s.edge * 100, 1) as edge_pct,
            ROUND(s.kelly_fraction, 4) as kelly,
            ROUND(s.bet_size_usd, 2) as bet_usd,
            s.regime_flags,
            s.confidence_multiplier as conf_mult,
            s.signal_type
        FROM signals s
        JOIN markets m ON s.market_id = m.id
        LEFT JOIN results r ON r.signal_id = s.id
        WHERE r.id IS NULL
        ORDER BY s.created_at DESC
    """)

    if len(active_df):
        total_exposure = active_df["bet_usd"].sum()

        c1, c2, c3 = st.columns(3)
        c1.metric("Open Positions", len(active_df))
        c2.metric("Capital at Risk", f"${total_exposure:,.2f}")
        c3.metric("Avg Edge", f"{active_df['edge_pct'].mean():.1f}%")

        st.divider()
        
        # Display adjustments for table
        display_df = active_df.copy()
        if "regime_flags" in display_df.columns:
            import json
            def _parse_flags(x):
                if not x: return "Clear"
                try:
                    d = json.loads(x) if isinstance(x, str) else x
                    flags = [k.replace("_", " ").title() for k, v in d.items() if v]
                    return " | ".join(flags) if flags else "Clear"
                except Exception:
                    return "Clear"
            
            display_df["Regime"] = display_df["regime_flags"].apply(_parse_flags)
            display_df = display_df.drop(columns=["regime_flags"])
            
        st.dataframe(display_df, use_container_width=True, hide_index=True)
    else:
        st.info(
            "No active bets currently open. Signals appear here when the "
            "scanner finds an edge and a bet is placed but not yet resolved."
        )


def _render_scanner() -> None:
    """Live market scanner — show current markets and passed bets."""

    st.subheader("🔎 Live Market Scanner")
    st.caption(
        "Scans Polymarket for weather markets, compares our forecast probabilities, "
        "and shows which edges we found — including those we **passed** on."
    )

    if st.button("▶️  Run Live Scan", type="primary", use_container_width=True):
        with st.spinner("Scanning Polymarket markets and fetching forecasts..."):
            try:
                from weatheredge.pipeline import run_scan

                signals, diag = asyncio.run(run_scan(diagnostics=True))

                if diag:
                    st.success(f"Scan complete — {diag.weather_markets_found} markets found")

                    # Summary row
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Markets Found", diag.weather_markets_found)
                    c2.metric("With Station", diag.markets_with_station)
                    c3.metric("Forecasts", diag.forecasts_fetched)
                    c4.metric("Bins Evaluated", diag.bins_evaluated)
                    c5.metric("Signals Found", diag.signals_above_threshold)

                    st.divider()

                    # ── Signals ───────────────────────────────────
                    if signals:
                        st.subheader("✅ Tradeable Signals")
                        sig_rows = []
                        for s in signals:
                            sig_rows.append({
                                "Station": s.station_id,
                                "Bin": s.bin_label,
                                "Model%": f"{s.model_prob:.1%}",
                                "Market%": f"{s.market_prob:.1%}",
                                "Edge%": f"{s.net_edge:+.1%}",
                                "Kelly": f"{s.kelly_fraction:.4f}",
                                "Bet $": f"${s.recommended_bet_usd:.2f}",
                                "Question": s.question[:50],
                            })
                        st.dataframe(pd.DataFrame(sig_rows), use_container_width=True, hide_index=True)

                    # ── Market Details (includes passed bets) ────
                    if diag.market_details:
                        st.divider()
                        st.subheader("📋 All Markets Evaluated (incl. Passed)")

                        rows = []
                        for md in diag.market_details:
                            skip = md.get("skip_reason", "")
                            sigs = md.get("signals_found", 0)
                            model_p = md.get("model_probs", [])
                            mkt_p = md.get("market_probs", [])

                            if model_p and mkt_p:
                                best_edge = max(
                                    mp - mk for mp, mk in zip(model_p, mkt_p)
                                )
                            else:
                                best_edge = None

                            status = "⏭️ Skipped" if skip else ("✅ Bet" if sigs > 0 else "❌ Passed")
                            
                            regime_info = "Clear"
                            if md.get("regime_flags"):
                                regime_info = " | ".join(k.replace("_", " ").title() for k, v in md["regime_flags"].items() if v)

                            rows.append({
                                "Status": status,
                                "Station": md.get("station_id", "???"),
                                "Regime": regime_info,
                                "Question": (md.get("question", ""))[:55],
                                "Best Raw Edge": f"{best_edge:+.1%}" if best_edge is not None else "—",
                                "Signals": sigs,
                                "Skip Reason": skip or "—",
                                "Calibrated": "✓" if md.get("calibration_applied") else "✗",
                            })

                        detail_df = pd.DataFrame(rows)
                        st.dataframe(detail_df, use_container_width=True, hide_index=True)

                    if diag.errors:
                        st.divider()
                        st.subheader("⚠️ Errors")
                        for e in diag.errors:
                            st.error(e)
                else:
                    st.warning("Scan returned no diagnostics.")

            except Exception as e:
                st.error(f"Scan failed: {e}")
    else:
        st.info("Click **Run Live Scan** to query current Polymarket weather markets.")

    # ── Currently tracked markets ─────────────────────────────────
    st.divider()
    st.subheader("🌐 Currently Tracked Markets")
    markets_df = _query("""
        SELECT
            m.station_id,
            m.question,
            m.metric,
            m.resolution_date,
            m.volume,
            m.resolved_outcome,
            m.created_at
        FROM markets m
        ORDER BY m.created_at DESC
        LIMIT 50
    """)
    if len(markets_df):
        st.dataframe(markets_df, use_container_width=True, hide_index=True)
    else:
        st.info("No markets tracked yet.")


def _render_history(cutoff: str | None, tf: str) -> None:
    """Resolved markets — bet vs passed."""

    st.subheader(f"🕰️ Resolved Market History ({tf})")

    time_filter = f"AND m.created_at >= '{cutoff}'" if cutoff else ""

    history_df = _query(f"""
        SELECT
            m.station_id,
            m.question,
            m.metric,
            m.resolution_date,
            m.resolved_outcome,
            m.volume,
            CASE
                WHEN s.id IS NOT NULL THEN '✅ Bet'
                ELSE '❌ Passed'
            END as action,
            ROUND(s.edge * 100, 1) as edge_pct,
            ROUND(s.bet_size_usd, 2) as bet_usd,
            r.won,
            ROUND(r.pnl_usd, 2) as pnl_usd,
            ROUND(bs.brier_score, 4) as brier
        FROM markets m
        LEFT JOIN signals s ON s.market_id = m.id AND s.signal_type != 'backtest'
        LEFT JOIN results r ON r.signal_id = s.id
        LEFT JOIN brier_scores bs ON bs.market_id = m.id
        WHERE m.resolved_outcome IS NOT NULL {time_filter}
        GROUP BY m.id
        ORDER BY m.resolution_date DESC
        LIMIT 100
    """)

    if len(history_df):
        # Summary metrics
        bet_count = len(history_df[history_df["action"] == "✅ Bet"])
        pass_count = len(history_df[history_df["action"] == "❌ Passed"])

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Resolved", len(history_df))
        c2.metric("Bet On", bet_count)
        c3.metric("Passed", pass_count)

        st.divider()
        st.dataframe(history_df, use_container_width=True, hide_index=True)
    else:
        st.info("No resolved markets for this timeframe.")


def _render_risk_guardrails() -> None:
    """Show detailed risk management status and safety checks."""
    st.subheader("🛡️ Risk Management & Safety")
    
    # ── Current Exposure ──────────────────────────────────────────
    active_df = _query("""
        SELECT SUM(s.bet_size_usd) as exposure, COUNT(*) as n
        FROM signals s
        LEFT JOIN results r ON r.signal_id = s.id
        WHERE r.id IS NULL
    """)
    current_exposure = float(active_df.iloc[0]["exposure"] or 0)
    current_positions = int(active_df.iloc[0]["n"] or 0)
    
    # In a real app we'd fetch actual bankroll, here we use config/default
    bankroll = 10000.0 # Placeholder for live bankroll
    exposure_pct = current_exposure / bankroll if bankroll > 0 else 0
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Current Exposure", f"${current_exposure:,.2f}", f"{exposure_pct:.1%}")
    c2.metric("Open Positions", current_positions)
    c3.metric("Bankroll Baseline", f"${bankroll:,.0f}")

    st.divider()
    
    # ── Kelly & Limits ───────────────────────────────────────────
    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown("### 📊 Sizing Parameters")
        st.info(f"""
        **Kelly Fraction**: {TRADING.kelly_fraction:.1%}  
        **Max Bet per Signal**: {TRADING.kelly_max_bet:.1%} (${bankroll * TRADING.kelly_max_bet:,.0f})  
        **Min Edge required**: {TRADING.min_edge_threshold:.1%}
        """)
        
    with col_right:
        st.markdown("### 🛑 Guardrails Status")
        # Sample check of position limits logic
        is_safe = current_exposure < (bankroll * 0.20) # 20% total cap
        status_icon = "✅" if is_safe else "⚠️"
        st.write(f"{status_icon} **Total Exposure Cap**: 20% (${bankroll * 0.20:,.0f})")
        
        is_qty_safe = current_positions < 10
        status_icon = "✅" if is_qty_safe else "⚠️"
        st.write(f"{status_icon} **Max Concurrent Positions**: 10")
        
    st.divider()
    
    # ── Regime Explainer ─────────────────────────────────────────
    st.markdown("### 🌪️ Atmospheric Regime Definitions")
    with st.expander("Learn about the risk flags..."):
        st.markdown("""
        - **Stale Data**: Mesonet observation is older than 2 hours. Strategy: *Drastically reduced edge/sizing.*
        - **Model Divergence**: Significant spread between high-res (HRES) and ensemble models. Strategy: *Reduced sizing.*
        - **High Volatility**: Predicted standard deviation is >2.5 sigma from climatology. Strategy: *Increased edge threshold.*
        - **Autumn Transition**: Seasonal shift detected (Advisory from Meteorologist). Strategy: *Manual confirmation override recommended.*
        """)


# ---------------------------------------------------------------------------
# Command Center – subprocess helpers
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def _init_cmd_state():
    """Ensure all command-center keys exist in session state."""
    for k, v in {
        "_cc_output": [],
        "_cc_status": "idle",
        "_cc_metrics": {},
        "_cc_command": "",
        "_cc_pid": None,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _start_cmd(cmd: str):
    """Launch *cmd* as a background subprocess."""
    _stop_cmd()
    st.session_state["_cc_output"] = []
    st.session_state["_cc_status"] = "running"
    st.session_state["_cc_metrics"] = {}
    st.session_state["_cc_command"] = cmd
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        st.session_state["_cc_pid"] = proc.pid
        t = threading.Thread(target=_reader_thread, args=(proc,), daemon=True)
        t.start()
    except Exception as e:
        st.session_state["_cc_output"] = [f"Failed to start: {e}\n"]
        st.session_state["_cc_status"] = "error"


def _reader_thread(proc: subprocess.Popen):
    """Read stdout line-by-line in a daemon thread."""
    try:
        for raw_line in proc.stdout:
            st.session_state["_cc_output"].append(raw_line)
            _parse_line_metrics(raw_line)
        proc.wait()
        st.session_state["_cc_status"] = (
            "done" if proc.returncode == 0 else "error"
        )
    except Exception:
        st.session_state["_cc_status"] = "error"


def _stop_cmd():
    """Terminate any running subprocess."""
    pid = st.session_state.get("_cc_pid")
    if pid:
        try:
            os.kill(pid, 15)  # SIGTERM
        except (ProcessLookupError, OSError):
            pass
    st.session_state["_cc_pid"] = None


def _parse_line_metrics(line: str):
    """Heuristically extract live metrics from stdout."""
    m = st.session_state["_cc_metrics"]
    s = line.strip()
    # Count market entries  ("[date] question...")
    if s.startswith("[") and "]" in s[:14]:
        m["markets_processed"] = m.get("markets_processed", 0) + 1
    # "Found X weather markets"
    if "weather markets" in s.lower():
        for w in s.split():
            if w.isdigit():
                m["total_markets"] = int(w)
                break
    if "P&L:" in line:
        try:
            m["last_pnl"] = line.split("P&L:")[1].split("|")[0].strip()
        except Exception:
            pass
    if "Bankroll:" in line and "$" in line:
        try:
            m["bankroll"] = line.split("Bankroll:")[1].strip().split()[0]
        except Exception:
            pass
    if "Signal:" in s and "Signals:" not in s:
        m["signals_count"] = m.get("signals_count", 0) + 1
    if s.startswith("Signals:"):
        try:
            m["final_signals"] = int(s.split(":")[1].strip())
        except Exception:
            pass
    if s.startswith("Total P&L:"):
        try:
            m["final_pnl"] = s.split(":")[1].strip()
        except Exception:
            pass
    if s.startswith("Avg Brier:"):
        try:
            m["avg_brier"] = s.split(":")[1].strip()
        except Exception:
            pass


def _html_esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Command Center – renderers
# ---------------------------------------------------------------------------


def _render_command_center():
    _init_cmd_state()

    h_col, t_col = st.columns([4, 1])
    with h_col:
        st.subheader("🖥️ Command Center")
    with t_col:
        terminal_mode = st.toggle(">_ Terminal", key="cc_terminal_toggle")

    status = st.session_state["_cc_status"]

    if status != "idle":
        _render_cmd_status_bar()

    st.divider()

    if terminal_mode:
        _render_terminal_mode()
    else:
        _render_gui_mode()

    # Auto-refresh while running
    if st.session_state["_cc_status"] == "running":
        time.sleep(1)
        st.rerun()


def _render_cmd_status_bar():
    """Live status banner + metrics + output log."""
    status = st.session_state["_cc_status"]
    cmd = st.session_state["_cc_command"]
    metrics = st.session_state["_cc_metrics"]

    if status == "running":
        st.markdown(
            f'<div class="status-running">🟢 <b>Running:</b> <code>{_html_esc(cmd)}</code></div>',
            unsafe_allow_html=True,
        )
        total = metrics.get("total_markets")
        processed = metrics.get("markets_processed", 0)
        if total and total > 0:
            st.progress(min(processed / total, 1.0),
                        text=f"Market {processed} / {total}")

        mcols = st.columns(4)
        mcols[0].metric("Markets", metrics.get("markets_processed", 0))
        mcols[1].metric("Signals", metrics.get("signals_count", 0))
        mcols[2].metric("Last P&L", metrics.get("last_pnl", "—"))
        mcols[3].metric("Bankroll", metrics.get("bankroll", "—"))

        if st.button("⏹️ Stop", type="secondary", use_container_width=True):
            _stop_cmd()
            st.session_state["_cc_status"] = "stopped"
            st.rerun()

    elif status == "done":
        st.markdown(
            f'<div class="status-done">✅ <b>Completed:</b> <code>{_html_esc(cmd)}</code></div>',
            unsafe_allow_html=True,
        )
        mcols = st.columns(4)
        mcols[0].metric("P&L", metrics.get("final_pnl",
                         metrics.get("last_pnl", "—")))
        mcols[1].metric("Signals", metrics.get("final_signals",
                         metrics.get("signals_count", "—")))
        mcols[2].metric("Markets", metrics.get("markets_processed", "—"))
        mcols[3].metric("Brier", metrics.get("avg_brier", "—"))

    elif status == "error":
        st.markdown(
            f'<div class="status-error">❌ <b>Error:</b> <code>{_html_esc(cmd)}</code></div>',
            unsafe_allow_html=True,
        )

    elif status == "stopped":
        st.info(f"⏹️ Stopped: `{cmd}`")

    lines = st.session_state["_cc_output"]
    if lines:
        with st.expander("📋 Output log", expanded=(status == "running")):
            clean = _ANSI_RE.sub("", "".join(lines))
            st.code(clean[-8000:], language=None)


def _render_gui_mode():
    """Friendly form panels for each CLI command."""
    busy = st.session_state["_cc_status"] == "running"
    py = sys.executable

    # ── Backtest ─────────────────────────────────────────────────
    with st.expander("🧪 **Backtest** — Replay historical markets", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            bt_start = st.date_input(
                "Start date",
                value=date.today() - timedelta(days=7),
                key="bt_start",
            )
            bt_source = st.selectbox(
                "Source", ["pmxt", "gamma"], key="bt_src",
                help="pmxt = archive.pmxt.dev · gamma = Polymarket API",
            )
        with c2:
            bt_end = st.date_input(
                "End date",
                value=date.today() - timedelta(days=1),
                key="bt_end",
            )
            bt_mode = st.selectbox(
                "Mode", ["brier_only", "full_sim"], key="bt_mode",
                help="brier_only = forecast skill · full_sim = P&L simulation",
            )
        c3, c4 = st.columns(2)
        with c3:
            bt_bank = st.number_input(
                "Bankroll ($)", value=10000, step=1000, key="bt_bank",
            )
        with c4:
            bt_model = st.selectbox(
                "Forecast model", ["ecmwf_ifs", "gfs_seamless"], key="bt_mdl",
            )
        if st.button("▶️ Run Backtest", disabled=busy,
                      use_container_width=True, type="primary"):
            cmd = (
                f"{py} -m weatheredge backtest "
                f"--start {bt_start} --end {bt_end} "
                f"--source {bt_source} --mode {bt_mode} "
                f"--bankroll {bt_bank} --model {bt_model}"
            )
            _start_cmd(cmd)
            st.rerun()

    # ── Scan + Diagnose ──────────────────────────────────────────
    lc, rc = st.columns(2)
    with lc:
        with st.expander("🔎 **Scan** — Find live trading signals"):
            sc_bank = st.number_input(
                "Bankroll ($)", value=10000, step=1000, key="sc_bank",
            )
            if st.button("▶️ Run Scan", disabled=busy,
                          use_container_width=True):
                _start_cmd(f"{py} -m weatheredge scan --bankroll {sc_bank}")
                st.rerun()
    with rc:
        with st.expander("🩺 **Diagnose** — Full diagnostic report"):
            dg_bank = st.number_input(
                "Bankroll ($)", value=10000, step=1000, key="dg_bank",
            )
            if st.button("▶️ Run Diagnose", disabled=busy,
                          use_container_width=True):
                _start_cmd(
                    f"{py} -m weatheredge diagnose --bankroll {dg_bank}")
                st.rerun()

    # ── Resolve + Inspect ────────────────────────────────────────
    lc2, rc2 = st.columns(2)
    with lc2:
        with st.expander("✅ **Resolve** — Score resolved markets"):
            st.caption("Check resolved markets and score predictions.")
            if st.button("▶️ Resolve Markets", disabled=busy,
                          use_container_width=True):
                _start_cmd(f"{py} -m weatheredge resolve")
                st.rerun()
    with rc2:
        with st.expander("📊 **Inspect** — Forecast vs observation"):
            ic1, ic2 = st.columns(2)
            with ic1:
                insp_station = st.text_input(
                    "Station ID", value="KLGA", key="insp_stn",
                )
                insp_metric = st.selectbox(
                    "Metric", ["tmax", "tmin"], key="insp_met",
                )
            with ic2:
                insp_date = st.date_input("Date", key="insp_dt")
                insp_dir = st.radio(
                    "Direction", ["over", "under"],
                    horizontal=True, key="insp_dir",
                )
            insp_thresh = st.number_input(
                "Threshold (°F)", value=70.0, key="insp_thr",
            )
            if st.button("▶️ Inspect", disabled=busy,
                          use_container_width=True):
                cmd = (
                    f"{py} -m weatheredge inspect {insp_station} {insp_date} "
                    f"--{insp_dir} {insp_thresh} --metric {insp_metric}"
                )
                _start_cmd(cmd)
                st.rerun()

    # ── Scheduler ────────────────────────────────────────────────
    with st.expander("▶️ **Scheduler** — Paper trading loop"):
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            sch_int = st.number_input(
                "Interval (min)", value=30, step=5, key="sch_int",
            )
        with sc2:
            sch_dur = st.number_input(
                "Duration (hrs)", value=0, step=1, key="sch_dur",
                help="0 = run indefinitely",
            )
        with sc3:
            sch_bank = st.number_input(
                "Bankroll ($)", value=10000, step=1000, key="sch_bank",
            )
        sch_once = st.checkbox("Single cycle only", key="sch_once")
        if st.button("▶️ Start Scheduler", disabled=busy,
                      use_container_width=True):
            cmd = (
                f"{py} -m weatheredge run "
                f"--interval {sch_int} --bankroll {sch_bank}"
            )
            if sch_dur > 0:
                cmd += f" --duration {sch_dur}"
            if sch_once:
                cmd += " --once"
            _start_cmd(cmd)
            st.rerun()


def _render_terminal_mode():
    """Raw terminal with command input and streamed output."""
    busy = st.session_state["_cc_status"] == "running"
    py = sys.executable

    cmd_input = st.text_input(
        "Command",
        value=f"{py} -m weatheredge ",
        key="terminal_cmd_input",
        placeholder="Enter command…",
    )

    bc1, bc2 = st.columns([3, 1])
    with bc1:
        if st.button("▶️ Run", disabled=busy,
                      use_container_width=True, type="primary"):
            if cmd_input.strip():
                _start_cmd(cmd_input.strip())
                st.rerun()
    with bc2:
        if st.button("⏹️ Stop", disabled=not busy,
                      use_container_width=True):
            _stop_cmd()
            st.session_state["_cc_status"] = "stopped"
            st.rerun()

    lines = st.session_state["_cc_output"]
    if lines:
        raw = "".join(lines)
        clean = _ANSI_RE.sub("", raw)
        st.markdown(
            f'<div class="terminal-output">{_html_esc(clean[-8000:])}</div>',
            unsafe_allow_html=True,
        )
    elif not busy:
        st.markdown(
            '<div class="terminal-output">$ Ready — enter a command above.\n</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
