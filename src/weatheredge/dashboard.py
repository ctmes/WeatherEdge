"""
WeatherEdge — Dashboard (Streamlit)

Launch:
    python -m weatheredge dashboard
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

_CSS = """
<style>
    /* Clean white background, no clutter */
    .stApp {
        background: #ffffff;
    }
    section[data-testid="stSidebar"] {
        background: #fafafa;
        border-right: 1px solid #e8e8e8;
    }

    /* Metric cards — flat, clean */
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

    /* Headings */
    h1 {
        color: #1a1a1a !important;
        font-weight: 600 !important;
        font-size: 1.6rem !important;
    }
    h3 {
        color: #1a1a1a !important;
        font-weight: 600 !important;
        font-size: 1.05rem !important;
        margin-bottom: 0.25rem !important;
    }

    /* Dataframes */
    div[data-testid="stDataFrame"] {
        border: 1px solid #e8e8e8;
        border-radius: 8px;
    }

    /* Dividers */
    hr {
        border-color: #f0f0f0 !important;
    }

    /* Caption text */
    .stCaption, [data-testid="stCaptionContainer"] {
        color: #999 !important;
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
            "Database not found. Run `python -m weatheredge run --once` "
            "to initialise."
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
    st.caption(
        f"{tf}  ·  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  {_refresh_note}"
    )

    # ── Metrics ───────────────────────────────────────────────────
    results_df = _query(
        """
        SELECT
            COUNT(*) as n,
            SUM(CASE WHEN r.won THEN 1 ELSE 0 END) as wins,
            SUM(r.pnl_usd) as total_pnl
        FROM results r
        JOIN signals s ON r.signal_id = s.id
        WHERE (? IS NULL OR r.resolved_at >= ?)
        """,
        (cutoff, cutoff),
    )

    brier_df = _query(
        """
        SELECT AVG(brier_score) as avg_brier, AVG(baseline_brier) as avg_baseline
        FROM brier_scores
        WHERE (? IS NULL OR calculated_at >= ?)
        """,
        (cutoff, cutoff),
    )

    n = int(results_df.iloc[0]["n"]) if len(results_df) and results_df.iloc[0]["n"] else 0
    wins = int(results_df.iloc[0]["wins"] or 0) if n else 0
    total_pnl = float(results_df.iloc[0]["total_pnl"] or 0) if n else 0
    win_rate = wins / n if n > 0 else 0

    avg_brier = float(brier_df.iloc[0]["avg_brier"]) if len(brier_df) and brier_df.iloc[0]["avg_brier"] is not None else None
    avg_baseline = float(brier_df.iloc[0]["avg_baseline"]) if len(brier_df) and brier_df.iloc[0]["avg_baseline"] is not None else None
    bss = analytics.brier_skill_score(avg_brier, avg_baseline) if avg_brier is not None and avg_baseline is not None else None

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("P&L", f"${total_pnl:,.2f}")
    c2.metric("Win Rate", f"{win_rate:.0%}" if n else "—")
    c3.metric("Trades", f"{n}")
    c4.metric("Brier Skill", f"{bss:+.1%}" if bss is not None else "—")

    # ── Active Positions ──────────────────────────────────────────
    st.divider()
    st.markdown("### Open Positions")

    active_df = _query("""
        SELECT
            s.created_at as opened,
            m.station_id as station,
            m.question,
            ROUND(s.edge * 100, 1) as edge_pct,
            ROUND(s.bet_size_usd, 2) as bet_usd,
            s.signal_type as type
        FROM signals s
        JOIN markets m ON s.market_id = m.id
        LEFT JOIN results r ON r.signal_id = s.id
        WHERE r.id IS NULL
          AND s.signal_type != 'backtest'
          AND (m.end_date IS NULL OR m.end_date > datetime('now', '-2 days'))
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
        total_exposure = active_df["bet_usd"].sum()
        st.caption(f"{len(active_df)} positions  ·  ${total_exposure:,.0f} at risk")
        st.dataframe(active_df, use_container_width=True, hide_index=True)
    else:
        st.caption("No open positions.")

    # ── Equity Curve ──────────────────────────────────────────────
    st.divider()
    st.markdown("### Equity Curve")

    equity_df = _query(
        """
        SELECT r.resolved_at,
               SUM(r.pnl_usd) OVER (ORDER BY r.resolved_at) as cumulative_pnl
        FROM results r
        JOIN signals s ON r.signal_id = s.id
        WHERE (? IS NULL OR r.resolved_at >= ?)
        ORDER BY r.resolved_at
        """,
        (cutoff, cutoff),
    )

    if len(equity_df) > 1:
        equity_df["resolved_at"] = pd.to_datetime(equity_df["resolved_at"], format="ISO8601")
        st.line_chart(
            equity_df.set_index("resolved_at")["cumulative_pnl"],
            use_container_width=True,
        )
    else:
        st.caption("Not enough data yet.")

    # ── Recent Trades ─────────────────────────────────────────────
    st.divider()
    st.markdown("### Recent Trades")

    trades_df = _query(
        """
        SELECT
            r.resolved_at as resolved,
            m.station_id as station,
            m.question,
            ROUND(s.edge * 100, 1) as edge_pct,
            ROUND(s.bet_size_usd, 2) as bet_usd,
            CASE WHEN r.won THEN '✓' ELSE '✗' END as won,
            ROUND(r.pnl_usd, 2) as pnl_usd
        FROM results r
        JOIN signals s ON r.signal_id = s.id
        JOIN markets m ON s.market_id = m.id
        WHERE (? IS NULL OR r.resolved_at >= ?)
        ORDER BY r.resolved_at DESC
        LIMIT 20
        """,
        (cutoff, cutoff),
    )

    if len(trades_df):
        st.dataframe(trades_df, use_container_width=True, hide_index=True)
    else:
        st.caption("No resolved trades in this window.")

    # ── Auto-refresh ──────────────────────────────────────────────
    if refresh_interval:
        st.markdown(
            f'<meta http-equiv="refresh" content="{refresh_interval}">',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
