"""
WeatherEdge — Dashboard (Streamlit)

Launch:
    python -m weatheredge dashboard
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from weatheredge import analytics
from weatheredge.config import DATABASE_URL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMEFRAMES = {
    "24h": timedelta(hours=24),
    "3 days": timedelta(days=3),
    "1 week": timedelta(weeks=1),
    "4 weeks": timedelta(weeks=4),
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


def _bin_label(outcomes_json, bin_index) -> str:
    """Return the outcome label for a given bin index."""
    try:
        outcomes = outcomes_json
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(outcomes, list):
            idx = int(bin_index) if bin_index is not None else 0
            if 0 <= idx < len(outcomes):
                return str(outcomes[idx])
    except Exception:
        pass
    return f"Bin {bin_index}"


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

_CSS = """
<style>
    .stApp { background: #ffffff; }
    section[data-testid="stSidebar"] {
        background: #fafafa;
        border-right: 1px solid #e8e8e8;
    }
    [data-testid="stMetric"] {
        background: #fafafa;
        border: 1px solid #e8e8e8;
        border-radius: 10px;
        padding: 18px 20px;
    }
    [data-testid="stMetricLabel"] {
        color: #8b8b8b;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-weight: 500;
    }
    [data-testid="stMetricValue"] {
        font-weight: 600;
        font-size: 1.5rem;
        color: #1a1a1a;
    }
    h1 { color: #1a1a1a !important; font-weight: 600 !important; font-size: 1.6rem !important; }
    h3 { color: #1a1a1a !important; font-weight: 600 !important; font-size: 1.05rem !important; margin-bottom: 0.25rem !important; }
    div[data-testid="stDataFrame"] { border: 1px solid #e8e8e8; border-radius: 8px; }
    hr { border-color: #f0f0f0 !important; }
    .stCaption, [data-testid="stCaptionContainer"] { color: #999 !important; }
    .stTabs [data-baseweb="tab-list"] { gap: 6px; }
    .stTabs [data-baseweb="tab"] {
        background: #fafafa;
        border: 1px solid #e8e8e8;
        border-radius: 6px;
        padding: 6px 18px;
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
        initial_sidebar_state="collapsed",
    )
    st.markdown(_CSS, unsafe_allow_html=True)

    db_path = _get_db_path()
    if not Path(db_path).exists():
        st.error(
            "Database not found. Run `python -m weatheredge run --once` to initialise."
        )
        return

    # ── Sidebar ───────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("**WeatherEdge**")
        tf = st.selectbox(
            "Timeframe",
            list(TIMEFRAMES.keys()),
            index=0,
            label_visibility="collapsed",
        )
        cutoff = _cutoff(tf)

        st.divider()
        if st.checkbox("Confirm reset", key="confirm_reset"):
            if st.button("Reset all data", use_container_width=True):
                conn = sqlite3.connect(_get_db_path())
                c = conn.cursor()
                for tbl in ("results", "signals", "brier_scores", "backtest_runs"):
                    c.execute(f"DELETE FROM {tbl}")
                conn.commit()
                conn.close()
                st.rerun()

        st.divider()
        _REFRESH_OPTIONS = {"Off": 0, "30s": 30, "1 min": 60, "5 min": 300}
        _refresh_label = st.selectbox(
            "Auto-refresh",
            list(_REFRESH_OPTIONS.keys()),
            index=1,
        )
        refresh_interval = _REFRESH_OPTIONS[_refresh_label]
        st.caption(f"DB: `{Path(db_path).name}`")

    # ── Header ────────────────────────────────────────────────────
    st.markdown("# WeatherEdge")
    _refresh_note = f"· refreshes every {_refresh_label}" if refresh_interval else "· auto-refresh off"
    st.caption(f"{tf}  ·  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  {_refresh_note}")

    # ── Load core trade data ───────────────────────────────────────
    trades_raw = _query("""
        SELECT
            s.model_prob, s.market_prob, s.edge, s.bet_size_usd, s.kelly_fraction,
            s.bin_index, m.outcomes,
            m.station_id, m.metric,
            r.won, r.pnl_usd, r.actual_slippage, r.resolved_at
        FROM results r
        JOIN signals s ON r.signal_id = s.id
        JOIN markets m ON s.market_id = m.id
        WHERE (? IS NULL OR r.resolved_at >= ?)
          AND s.signal_type != 'backtest'
    """, (cutoff, cutoff))

    brier_summary = _query("""
        SELECT AVG(brier_score) as avg_brier, AVG(baseline_brier) as avg_baseline
        FROM brier_scores
        WHERE (? IS NULL OR calculated_at >= ?)
    """, (cutoff, cutoff))

    # ── Derive scalars ─────────────────────────────────────────────
    n = len(trades_raw)
    if n > 0:
        wins = int(trades_raw["won"].sum())
        losses = n - wins
        total_pnl = float(trades_raw["pnl_usd"].sum())
        win_rate = wins / n
        avg_win = float(trades_raw.loc[trades_raw["won"] == 1, "pnl_usd"].mean()) if wins else 0.0
        avg_loss = float(trades_raw.loc[trades_raw["won"] == 0, "pnl_usd"].mean()) if losses else 0.0
        gross_profit = trades_raw.loc[trades_raw["won"] == 1, "pnl_usd"].sum()
        gross_loss = abs(trades_raw.loc[trades_raw["won"] == 0, "pnl_usd"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        expectancy = total_pnl / n
        avg_edge = float(trades_raw["edge"].mean())
        win_ci = analytics.win_rate_confidence_interval(wins, n)
    else:
        wins = losses = 0
        total_pnl = win_rate = avg_win = avg_loss = profit_factor = expectancy = avg_edge = 0.0
        win_ci = (0.0, 1.0)

    avg_brier_val = (
        float(brier_summary.iloc[0]["avg_brier"])
        if len(brier_summary) and brier_summary.iloc[0]["avg_brier"] is not None
        else None
    )
    avg_baseline_val = (
        float(brier_summary.iloc[0]["avg_baseline"])
        if len(brier_summary) and brier_summary.iloc[0]["avg_baseline"] is not None
        else None
    )
    bss = (
        analytics.brier_skill_score(avg_brier_val, avg_baseline_val)
        if avg_brier_val is not None and avg_baseline_val is not None
        else None
    )

    # ── Equity & risk ─────────────────────────────────────────────
    equity_df = _query("""
        SELECT r.resolved_at,
               r.pnl_usd,
               SUM(r.pnl_usd) OVER (ORDER BY r.resolved_at) as cumulative_pnl
        FROM results r
        JOIN signals s ON r.signal_id = s.id
        WHERE (? IS NULL OR r.resolved_at >= ?)
          AND s.signal_type != 'backtest'
        ORDER BY r.resolved_at
    """, (cutoff, cutoff))

    daily_pnl_df = _query("""
        SELECT DATE(r.resolved_at) as day, SUM(r.pnl_usd) as daily_pnl
        FROM results r
        JOIN signals s ON r.signal_id = s.id
        WHERE (? IS NULL OR r.resolved_at >= ?)
          AND s.signal_type != 'backtest'
        GROUP BY DATE(r.resolved_at)
        ORDER BY day
    """, (cutoff, cutoff))

    if len(equity_df) > 1:
        cum_vals = equity_df["cumulative_pnl"].values.astype(float)
        mdd = analytics.max_drawdown(cum_vals.tolist())
    else:
        mdd = 0.0

    sharpe = (
        analytics.sharpe_ratio(daily_pnl_df["daily_pnl"].tolist())
        if len(daily_pnl_df) > 1
        else 0.0
    )

    # ── Tabs ──────────────────────────────────────────────────────
    tab_overview, tab_positions, tab_analysis, tab_trades = st.tabs([
        "Overview", "Open Positions", "Analysis", "Trades & Scores",
    ])

    # =================================================================
    # TAB 1 — OVERVIEW
    # =================================================================
    with tab_overview:
        # Row 1: P&L + win stats
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total P&L", f"${total_pnl:,.2f}")
        c2.metric(
            "Win Rate",
            f"{win_rate:.1%}" if n else "—",
            delta=f"CI [{win_ci[0]:.0%}–{win_ci[1]:.0%}]" if n >= 5 else None,
            delta_color="off",
        )
        c3.metric("Trades", str(n))
        c4.metric("Wins / Losses", f"{wins} / {losses}" if n else "—")
        c5.metric("Avg Win", f"${avg_win:,.2f}" if wins else "—")
        c6.metric("Avg Loss", f"${avg_loss:,.2f}" if losses else "—")

        st.divider()

        # Row 2: Risk + quality
        c7, c8, c9, c10, c11, c12 = st.columns(6)
        c7.metric(
            "Profit Factor",
            f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞" if n else "—",
        )
        c8.metric("Expectancy / Trade", f"${expectancy:,.2f}" if n else "—")
        c9.metric("Sharpe (ann.)", f"{sharpe:.2f}" if n > 1 else "—")
        c10.metric("Max Drawdown", f"{mdd:.1%}" if n > 1 else "—")
        c11.metric("Avg Edge", f"{avg_edge:.1%}" if n else "—")
        c12.metric("Brier Skill", f"{bss:+.1%}" if bss is not None else "—")

        st.divider()

        # Equity + drawdown side-by-side
        if len(equity_df) > 1:
            equity_df["resolved_at"] = pd.to_datetime(equity_df["resolved_at"], format="ISO8601")
            equity_idx = equity_df.set_index("resolved_at")

            col_eq, col_dd = st.columns(2)
            with col_eq:
                st.markdown("### Equity Curve")
                st.line_chart(equity_idx["cumulative_pnl"], use_container_width=True)

            with col_dd:
                st.markdown("### Drawdown")
                cum = equity_idx["cumulative_pnl"].values.astype(float)
                peak = np.maximum.accumulate(cum)
                dd_pct = np.where(peak > 0, (cum - peak) / peak * 100, 0.0)
                dd_df = pd.DataFrame({"drawdown_%": dd_pct}, index=equity_idx.index)
                st.line_chart(dd_df, use_container_width=True)
        else:
            st.caption("Not enough resolved trades for charts yet.")

        # Rolling win rate
        if n >= 10:
            st.divider()
            st.markdown("### Rolling Win Rate (30-trade window)")
            rolling = trades_raw.copy()
            rolling["resolved_at"] = pd.to_datetime(rolling["resolved_at"], format="ISO8601")
            rolling = rolling.sort_values("resolved_at")
            rolling["rolling_wr"] = rolling["won"].rolling(30, min_periods=5).mean()
            rolling = rolling.set_index("resolved_at").dropna(subset=["rolling_wr"])
            st.line_chart(rolling["rolling_wr"], use_container_width=True)

    # =================================================================
    # TAB 2 — OPEN POSITIONS
    # =================================================================
    with tab_positions:
        active_df = _query("""
            SELECT
                s.created_at as opened,
                m.station_id as station,
                m.metric,
                m.question,
                s.bin_index,
                m.outcomes,
                ROUND(s.model_prob * 100, 1) as model_pct,
                ROUND(s.market_prob * 100, 1) as mkt_pct,
                ROUND(s.edge * 100, 1) as edge_pct,
                ROUND(s.kelly_fraction * 100, 2) as kelly_pct,
                ROUND(s.bet_size_usd, 2) as bet_usd,
                m.end_date
            FROM signals s
            JOIN markets m ON s.market_id = m.id
            LEFT JOIN results r ON r.signal_id = s.id
            WHERE r.id IS NULL
              AND s.signal_type != 'backtest'
              AND s.id = (
                  SELECT id FROM signals s2
                  WHERE s2.market_id = s.market_id
                    AND s2.bin_index = s.bin_index
                    AND s2.signal_type != 'backtest'
                  ORDER BY s2.created_at DESC
                  LIMIT 1
              )
            ORDER BY s.created_at DESC
        """)

        if len(active_df):
            active_df["bet_outcome"] = active_df.apply(
                lambda r: _bin_label(r["outcomes"], r["bin_index"]), axis=1
            )
            display_cols = ["opened", "station", "metric", "question", "bet_outcome",
                            "model_pct", "mkt_pct", "edge_pct", "kelly_pct", "bet_usd", "end_date"]
            display_active = active_df[[c for c in display_cols if c in active_df.columns]]
            total_exposure = active_df["bet_usd"].sum()
            avg_pos_edge = active_df["edge_pct"].mean()
            p1, p2, p3 = st.columns(3)
            p1.metric("Open Positions", str(len(active_df)))
            p2.metric("Total Exposure", f"${total_exposure:,.2f}")
            p3.metric("Avg Edge", f"{avg_pos_edge:.1f}%")
            st.dataframe(display_active, use_container_width=True, hide_index=True)
        else:
            st.caption("No open positions.")

    # =================================================================
    # TAB 3 — ANALYSIS
    # =================================================================
    with tab_analysis:
        if n == 0:
            st.caption("No resolved trades in this window — nothing to analyse.")
        else:
            # Station breakdown
            st.markdown("### Performance by Station")
            station_df = _query("""
                SELECT
                    m.station_id,
                    COUNT(*) as trades,
                    SUM(CASE WHEN r.won THEN 1 ELSE 0 END) as wins,
                    ROUND(SUM(r.pnl_usd), 2) as pnl,
                    ROUND(AVG(s.edge), 4) as avg_edge,
                    ROUND(AVG(s.bet_size_usd), 2) as avg_bet
                FROM results r
                JOIN signals s ON r.signal_id = s.id
                JOIN markets m ON s.market_id = m.id
                WHERE (? IS NULL OR r.resolved_at >= ?)
                  AND s.signal_type != 'backtest'
                GROUP BY m.station_id
                ORDER BY pnl DESC
            """, (cutoff, cutoff))

            if len(station_df):
                station_df["win_rate"] = (station_df["wins"] / station_df["trades"]).map("{:.0%}".format)
                station_df["avg_edge"] = station_df["avg_edge"].map("{:.1%}".format)
                col_st, col_bar = st.columns([3, 2])
                with col_st:
                    st.dataframe(station_df.drop(columns=["wins"]), use_container_width=True, hide_index=True)
                with col_bar:
                    st.bar_chart(station_df.set_index("station_id")["pnl"], use_container_width=True)

            st.divider()

            # Metric breakdown
            st.markdown("### Performance by Weather Metric")
            metric_df = _query("""
                SELECT
                    m.metric,
                    COUNT(*) as trades,
                    SUM(CASE WHEN r.won THEN 1 ELSE 0 END) as wins,
                    ROUND(SUM(r.pnl_usd), 2) as pnl,
                    ROUND(AVG(s.edge), 4) as avg_edge
                FROM results r
                JOIN signals s ON r.signal_id = s.id
                JOIN markets m ON s.market_id = m.id
                WHERE (? IS NULL OR r.resolved_at >= ?)
                  AND s.signal_type != 'backtest'
                GROUP BY m.metric
                ORDER BY pnl DESC
            """, (cutoff, cutoff))

            if len(metric_df):
                metric_df["win_rate"] = (metric_df["wins"] / metric_df["trades"]).map("{:.0%}".format)
                metric_df["avg_edge"] = metric_df["avg_edge"].map("{:.1%}".format)
                col_m1, col_m2 = st.columns([2, 1])
                with col_m1:
                    st.dataframe(metric_df.drop(columns=["wins"]), use_container_width=True, hide_index=True)
                with col_m2:
                    st.bar_chart(metric_df.set_index("metric")["pnl"], use_container_width=True)

            st.divider()

            # Calibration
            st.markdown("### Model Calibration")
            st.caption("Predicted probability vs actual win rate — a well-calibrated model sits on the diagonal.")

            calib_raw = _query("""
                SELECT s.model_prob, r.won
                FROM results r
                JOIN signals s ON r.signal_id = s.id
                WHERE (? IS NULL OR r.resolved_at >= ?)
                  AND s.signal_type != 'backtest'
                  AND s.model_prob IS NOT NULL
            """, (cutoff, cutoff))

            if len(calib_raw) >= 10:
                diagram = analytics.reliability_diagram_data(
                    calib_raw["model_prob"].tolist(),
                    calib_raw["won"].astype(int).tolist(),
                    n_bins=8,
                )
                if diagram:
                    ece = analytics.expected_calibration_error(
                        calib_raw["model_prob"].tolist(),
                        calib_raw["won"].astype(int).tolist(),
                    )
                    st.caption(f"Expected Calibration Error (ECE): {ece:.3f}  ·  lower = better")
                    calib_chart = pd.DataFrame({
                        "model": [d["predicted_mean"] for d in diagram],
                        "actual": [d["actual_frequency"] for d in diagram],
                        "perfect": [d["predicted_mean"] for d in diagram],
                    }).set_index("model")
                    st.line_chart(calib_chart, use_container_width=True)
            else:
                st.caption("Need ≥10 resolved trades for calibration chart.")

            st.divider()

            # Distributions
            st.markdown("### Signal Distributions")
            col_d1, col_d2 = st.columns(2)

            with col_d1:
                st.caption("Bet size distribution ($)")
                bet_vals = trades_raw["bet_size_usd"].dropna().values
                if len(bet_vals):
                    counts, bins = np.histogram(bet_vals, bins=10)
                    hist_df = pd.DataFrame({
                        "bet_$": [f"${b:.0f}" for b in bins[:-1]],
                        "count": counts,
                    }).set_index("bet_$")
                    st.bar_chart(hist_df, use_container_width=True)

            with col_d2:
                st.caption("Edge distribution (%)")
                edge_vals = (trades_raw["edge"].dropna() * 100).values
                if len(edge_vals):
                    counts, bins = np.histogram(edge_vals, bins=10)
                    hist_df = pd.DataFrame({
                        "edge_%": [f"{b:.1f}%" for b in bins[:-1]],
                        "count": counts,
                    }).set_index("edge_%")
                    st.bar_chart(hist_df, use_container_width=True)

            # Edge vs outcome scatter summary
            st.divider()
            st.markdown("### Edge Buckets — Predicted vs Actual")
            st.caption("Does higher edge correlate with better outcomes?")
            if n >= 10:
                bucket_df = trades_raw.copy()
                bucket_df["edge_bucket"] = pd.cut(
                    bucket_df["edge"] * 100,
                    bins=[-100, 0, 5, 10, 20, 100],
                    labels=["<0%", "0–5%", "5–10%", "10–20%", ">20%"],
                )
                summary = (
                    bucket_df.groupby("edge_bucket", observed=True)
                    .agg(trades=("won", "count"), win_rate=("won", "mean"), total_pnl=("pnl_usd", "sum"))
                    .reset_index()
                )
                summary["win_rate"] = summary["win_rate"].map("{:.1%}".format)
                summary["total_pnl"] = summary["total_pnl"].map("${:,.2f}".format)
                st.dataframe(summary, use_container_width=True, hide_index=True)

    # =================================================================
    # TAB 4 — TRADES & SCORES
    # =================================================================
    with tab_trades:
        st.markdown("### Recent Trades")
        recent_df = _query("""
            SELECT
                r.resolved_at as resolved,
                m.station_id as station,
                m.metric,
                m.question,
                s.bin_index,
                m.outcomes,
                ROUND(s.model_prob * 100, 1) as model_pct,
                ROUND(s.market_prob * 100, 1) as mkt_pct,
                ROUND(s.edge * 100, 1) as edge_pct,
                ROUND(s.bet_size_usd, 2) as bet_usd,
                CASE WHEN r.won THEN '✓' ELSE '✗' END as won,
                ROUND(r.pnl_usd, 2) as pnl_usd,
                ROUND(r.actual_slippage * 100, 3) as slip_pct
            FROM results r
            JOIN signals s ON r.signal_id = s.id
            JOIN markets m ON s.market_id = m.id
            WHERE (? IS NULL OR r.resolved_at >= ?)
              AND s.signal_type != 'backtest'
            ORDER BY r.resolved_at DESC
            LIMIT 50
        """, (cutoff, cutoff))

        if len(recent_df):
            recent_df["bet_outcome"] = recent_df.apply(
                lambda r: _bin_label(r["outcomes"], r["bin_index"]), axis=1
            )
            display_cols = ["resolved", "station", "metric", "question", "bet_outcome",
                            "model_pct", "mkt_pct", "edge_pct", "bet_usd", "won", "pnl_usd", "slip_pct"]
            st.dataframe(
                recent_df[[c for c in display_cols if c in recent_df.columns]],
                use_container_width=True, hide_index=True
            )
        else:
            st.caption("No resolved trades in this window.")

        st.divider()
        st.markdown("### Brier Scores by Market")
        brier_detail = _query("""
            SELECT
                bs.market_id,
                m.station_id,
                m.metric,
                ROUND(bs.brier_score, 4) as brier,
                ROUND(bs.baseline_brier, 4) as baseline,
                ROUND(bs.rps, 4) as rps,
                ROUND(bs.log_score, 4) as log_score,
                bs.calculated_at
            FROM brier_scores bs
            LEFT JOIN markets m ON bs.market_id = m.id
            WHERE (? IS NULL OR bs.calculated_at >= ?)
            ORDER BY bs.calculated_at DESC
            LIMIT 50
        """, (cutoff, cutoff))

        if len(brier_detail):
            st.dataframe(brier_detail, use_container_width=True, hide_index=True)
        else:
            st.caption("No Brier scores in this window.")

    # ── Auto-refresh (preserves tab state via st.rerun, not browser reload) ──
    if refresh_interval:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=refresh_interval * 1000, key="auto_refresh")
        except ImportError:
            st.sidebar.caption("Install `streamlit-autorefresh` for tab-safe refresh.")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
