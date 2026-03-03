"""
Lightweight Streamlit dashboard — read-only view into paper trading data.

Launch:
    python -m weatheredge dashboard
    # or:  streamlit run src/weatheredge/dashboard.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
from weatheredge import analytics
from weatheredge.config import DATABASE_URL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db_path() -> str:
    """Extract the file path from the SQLAlchemy URL."""
    # DATABASE_URL looks like: sqlite+aiosqlite:///path/to/weatheredge.db
    raw = DATABASE_URL
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return "weatheredge.db"


def _query(sql: str, db_path: str | None = None) -> pd.DataFrame:
    """Run a SQL query and return a DataFrame."""
    path = db_path or _get_db_path()
    if not Path(path).exists():
        return pd.DataFrame()
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dashboard pages
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="WeatherEdge Dashboard",
        page_icon="🌤️",
        layout="wide",
    )

    st.title("🌤️ WeatherEdge — Paper Trading Dashboard")

    db_path = _get_db_path()
    if not Path(db_path).exists():
        st.warning(
            "Database not found. Run `python -m weatheredge run --once` "
            "to initialise and collect first data."
        )
        return

    # ── Header stats ──────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    signals_df = _query("SELECT COUNT(*) as n FROM signals")
    total_signals = int(signals_df.iloc[0]["n"]) if len(signals_df) else 0
    col1.metric("Total Signals", total_signals)

    results_df = _query("SELECT COUNT(*) as n, SUM(CASE WHEN won THEN 1 ELSE 0 END) as wins, SUM(pnl_usd) as pnl FROM results")
    bt_stats = _query("SELECT sharpe_ratio, max_drawdown FROM backtest_runs ORDER BY finished_at DESC LIMIT 1")
    
    if len(results_df) and results_df.iloc[0]["n"]:
        total_resolved = int(results_df.iloc[0]["n"])
        wins = int(results_df.iloc[0]["wins"] or 0)
        pnl = float(results_df.iloc[0]["pnl"] or 0)
        win_rate = wins / total_resolved if total_resolved > 0 else 0
        
        low_ci, high_ci = analytics.win_rate_confidence_interval(wins, total_resolved)

        col2.metric("Resolved", total_resolved)
        col3.metric("Win Rate", f"{win_rate:.1%}", help=f"95% CI: [{low_ci:.1%} - {high_ci:.1%}]")
        col4.metric("Paper P&L", f"${pnl:,.2f}")
    else:
        col2.metric("Resolved", 0)
        col3.metric("Win Rate", "—")
        col4.metric("Paper P&L", "—")

    if len(bt_stats) and not bt_stats.iloc[0].isna().all():
        st.divider()
        c1, c2 = st.columns(2)
        sharpe = bt_stats.iloc[0]["sharpe_ratio"]
        mdd = bt_stats.iloc[0]["max_drawdown"]
        c1.metric("Sharpe Ratio", f"{sharpe:.2f}" if sharpe is not None else "—")
        c2.metric("Max Drawdown", f"{mdd*100:.1f}%" if mdd is not None else "—")

    st.divider()

    # ── Recent signals ────────────────────────────────────────────
    st.subheader("📊 Recent Signals")
    recent = _query("""
        SELECT
            s.created_at,
            m.station_id,
            m.question,
            s.bin_index,
            ROUND(s.model_prob * 100, 1) as model_pct,
            ROUND(s.market_prob * 100, 1) as market_pct,
            ROUND(s.edge * 100, 1) as edge_pct,
            ROUND(s.kelly_fraction, 4) as kelly,
            ROUND(s.bet_size_usd, 2) as bet_usd
        FROM signals s
        JOIN markets m ON s.market_id = m.id
        ORDER BY s.created_at DESC
        LIMIT 50
    """)

    if len(recent):
        st.dataframe(recent, use_container_width=True)
    else:
        st.info("No signals logged yet. Run a scan first.")

    # ── Brier score trend ─────────────────────────────────────────
    st.subheader("🎯 Brier Score Trend")
    brier_df = _query("""
        SELECT calculated_at, brier_score
        FROM brier_scores
        ORDER BY calculated_at
    """)

    if len(brier_df) > 1:
        brier_df["calculated_at"] = pd.to_datetime(brier_df["calculated_at"])
        st.line_chart(brier_df.set_index("calculated_at")["brier_score"])
    else:
        st.info("Not enough resolved markets for Brier score trend.")

    # ── Equity Curve ──────────────────────────────────────────────
    st.subheader("📈 Equity Curve")
    equity_df = _query("""
        SELECT r.resolved_at, SUM(r.pnl_usd) OVER (ORDER BY r.resolved_at) as cumulative_pnl
        FROM results r
        JOIN signals s ON r.signal_id = s.id
        ORDER BY r.resolved_at
    """)
    if len(equity_df) > 0:
        equity_df["resolved_at"] = pd.to_datetime(equity_df["resolved_at"])
        st.line_chart(equity_df.set_index("resolved_at")["cumulative_pnl"])
    else:
        st.info("No P&L data to chart yet.")

    # ── Reliability Diagram ───────────────────────────────────────
    st.subheader("🎯 Calibration (Reliability Diagram)")
    calib_df = _query("""
        SELECT s.model_prob, CASE WHEN r.won THEN 1 ELSE 0 END as won
        FROM signals s
        JOIN results r ON r.signal_id = s.id
    """)
    if len(calib_df) > 10:
        bin_data = analytics.reliability_diagram_data(
            calib_df["model_prob"].tolist(),
            calib_df["won"].tolist(),
            n_bins=10
        )
        plot_df = pd.DataFrame(bin_data)
        
        # Plot predicted vs actual
        st.bar_chart(plot_df.set_index("bin_center")[["actual_frequency", "predicted_mean"]])
        st.caption("Perfect calibration: Actual Frequency (blue) matches Predicted Mean (red).")
    else:
        st.info("Need more resolved samples (>10) for reliability diagram.")

    # ── Station breakdown ─────────────────────────────────────────
    st.subheader("🏢 Performance by Station")
    station_df = _query("""
        SELECT
            m.station_id,
            COUNT(*) as signals,
            SUM(CASE WHEN r.won THEN 1 ELSE 0 END) as wins,
            ROUND(AVG(CASE WHEN r.won IS NOT NULL THEN r.pnl_usd END), 2) as avg_pnl
        FROM signals s
        JOIN markets m ON s.market_id = m.id
        LEFT JOIN results r ON r.signal_id = s.id
        WHERE m.station_id IS NOT NULL
        GROUP BY m.station_id
        ORDER BY signals DESC
    """)

    if len(station_df):
        st.dataframe(station_df, use_container_width=True)
    else:
        st.info("No station data yet.")

    # ── Markets overview ──────────────────────────────────────────
    st.subheader("🌐 Markets Tracked")
    markets_df = _query("""
        SELECT id, station_id, question, created_at
        FROM markets
        ORDER BY created_at DESC
        LIMIT 30
    """)
    if len(markets_df):
        st.dataframe(markets_df, use_container_width=True)


if __name__ == "__main__":
    main()
