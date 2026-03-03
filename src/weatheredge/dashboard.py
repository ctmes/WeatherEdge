"""
WeatherEdge —  Dashboard (Streamlit)

Launch:
    python -m weatheredge dashboard
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
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
    tab_perf, tab_active, tab_scanner, tab_history = st.tabs([
        "📈 Performance",
        "💼 Active Bets",
        "🔎 Market Scanner",
        "🕰️ Market History",
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
    # TAB 3: MARKET SCANNER
    # ==================================================================
    with tab_scanner:
        _render_scanner()

    # ==================================================================
    # TAB 4: MARKET HISTORY
    # ==================================================================
    with tab_history:
        _render_history(cutoff, tf)


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
        SELECT AVG(brier_score) as avg_brier
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
    c8.metric("Avg Brier", f"{avg_brier:.4f}" if avg_brier is not None else "—")
    c9.metric("Volume", f"${total_volume:,.0f}")
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
        st.dataframe(active_df, use_container_width=True, hide_index=True)
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

                            rows.append({
                                "Status": status,
                                "Station": md.get("station_id", "???"),
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


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
