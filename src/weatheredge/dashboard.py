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

_REFRESH_OPTIONS = {"Off": 0, "30s": 30, "1 min": 60, "5 min": 300}


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
    /* ── Base font (avoid overriding icon fonts) ────────────────────────── */
    html, body, .stApp, .stMarkdown, p, li, label, button,
    [data-testid], [class^="st"] {
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
                     "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
    }
    /* Restore Material Symbols (broken by broad font rules) */
    .material-symbols-rounded,
    .material-symbols-outlined,
    .material-icons {
        font-family: 'Material Symbols Rounded', 'Material Symbols Outlined',
                     'Material Icons' !important;
    }

    /* ── Page ──────────────────────────────────────────────────────────── */
    .stApp { background: #F5F5F7; }
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
    }

    /* ── Sidebar (admin only) ──────────────────────────────────────────── */
    section[data-testid="stSidebar"] {
        background: rgba(255, 255, 255, 0.96);
        border-right: 1px solid rgba(0, 0, 0, 0.07);
    }

    /* ── KPI cards ─────────────────────────────────────────────────────── */
    .kpi-card {
        background: #FFFFFF;
        border: 1px solid rgba(0, 0, 0, 0.06);
        border-radius: 16px;
        padding: 18px 20px 20px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
        min-height: 96px;
    }
    .kpi-label {
        color: #86868B;
        font-size: 0.67rem;
        text-transform: uppercase;
        letter-spacing: 0.09em;
        font-weight: 600;
        margin-bottom: 8px;
    }
    .kpi-value {
        font-weight: 600;
        font-size: 1.5rem;
        letter-spacing: -0.02em;
        color: #1D1D1F;
        line-height: 1.1;
    }
    .kpi-sub {
        font-size: 0.68rem;
        color: #86868B;
        margin-top: 5px;
        font-weight: 500;
        letter-spacing: 0.01em;
    }

    /* ── Streamlit native metrics (fallback) ───────────────────────────── */
    [data-testid="stMetric"] {
        background: #FFFFFF;
        border: 1px solid rgba(0, 0, 0, 0.06);
        border-radius: 16px;
        padding: 18px 20px;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04);
        min-height: 96px;
    }
    [data-testid="stMetricLabel"] {
        color: #86868B;
        font-size: 0.67rem;
        text-transform: uppercase;
        letter-spacing: 0.09em;
        font-weight: 600;
    }
    [data-testid="stMetricValue"] {
        font-weight: 600;
        font-size: 1.5rem;
        color: #1D1D1F;
        letter-spacing: -0.02em;
    }
    [data-testid="stMetricDelta"] svg { display: none; }
    [data-testid="stMetricDelta"] > div {
        color: #86868B !important;
        font-size: 0.68rem;
        font-weight: 500;
    }

    /* ── Typography ────────────────────────────────────────────────────── */
    h1 {
        color: #1D1D1F !important;
        font-weight: 700 !important;
        font-size: 1.75rem !important;
        letter-spacing: -0.03em !important;
        margin-bottom: 0 !important;
    }
    h2 {
        color: #1D1D1F !important;
        font-weight: 600 !important;
        font-size: 1.1rem !important;
        letter-spacing: -0.02em !important;
        margin-top: 0.25rem !important;
        margin-bottom: 0.75rem !important;
    }
    h3 {
        color: #1D1D1F !important;
        font-weight: 600 !important;
        font-size: 0.88rem !important;
        letter-spacing: 0.04em !important;
        text-transform: uppercase !important;
        color: #86868B !important;
        margin-bottom: 0.5rem !important;
    }

    /* ── Section header with accent bar ────────────────────────────────── */
    .section-heading {
        font-size: 1.05rem;
        font-weight: 600;
        color: #1D1D1F;
        letter-spacing: -0.02em;
        padding-left: 12px;
        border-left: 3px solid #007AFF;
        margin: 0 0 1rem 0;
        line-height: 1.3;
    }
    .section-caption {
        font-size: 0.78rem;
        color: #86868B;
        margin-top: -0.75rem;
        margin-bottom: 1rem;
        padding-left: 15px;
    }

    /* ── Page header bar ───────────────────────────────────────────────── */
    .page-header {
        display: flex;
        align-items: baseline;
        gap: 12px;
        margin-bottom: 0.25rem;
    }
    .page-title {
        font-size: 1.75rem;
        font-weight: 700;
        color: #1D1D1F;
        letter-spacing: -0.03em;
    }
    .page-meta {
        font-size: 0.78rem;
        color: #86868B;
        font-weight: 400;
    }

    /* ── Dividers ──────────────────────────────────────────────────────── */
    hr {
        border: none !important;
        border-top: 1px solid rgba(0, 0, 0, 0.07) !important;
        margin: 1.5rem 0 !important;
    }

    /* ── Captions ──────────────────────────────────────────────────────── */
    .stCaption, [data-testid="stCaptionContainer"] {
        color: #86868B !important;
        font-size: 0.78rem !important;
    }

    /* ── DataFrames ────────────────────────────────────────────────────── */
    div[data-testid="stDataFrame"] {
        border: 1px solid rgba(0, 0, 0, 0.06) !important;
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
        background: #FFFFFF;
    }

    /* ── Charts ────────────────────────────────────────────────────────── */
    [data-testid="stArrowVegaLiteChart"],
    [data-testid="stVegaLiteChart"] {
        background: #FFFFFF;
        border-radius: 14px;
        padding: 8px 10px;
        border: 1px solid rgba(0, 0, 0, 0.06);
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
    }

    /* ── Tabs — iOS segmented control ──────────────────────────────────── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        background: rgba(0, 0, 0, 0.05);
        border-radius: 10px;
        padding: 3px;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent;
        border: none;
        border-radius: 8px;
        padding: 7px 22px;
        color: #6E6E73;
        font-weight: 500;
        font-size: 0.875rem;
        letter-spacing: -0.01em;
    }
    .stTabs [aria-selected="true"] {
        background: #FFFFFF !important;
        color: #1D1D1F !important;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.14), 0 1px 2px rgba(0, 0, 0, 0.08) !important;
    }

    /* ── Selectbox ─────────────────────────────────────────────────────── */
    [data-testid="stSelectbox"] > div > div {
        border-radius: 8px !important;
        border-color: rgba(0, 0, 0, 0.1) !important;
        background: #FFFFFF !important;
    }

    /* ── Text inputs ───────────────────────────────────────────────────── */
    [data-testid="stTextInput"] > div > div {
        border-radius: 8px !important;
        border-color: rgba(0, 0, 0, 0.1) !important;
        background: #FFFFFF !important;
    }

    /* ── Buttons ───────────────────────────────────────────────────────── */
    [data-testid="stButton"] > button {
        border-radius: 8px !important;
        border-color: rgba(0, 0, 0, 0.1) !important;
        background: #FFFFFF !important;
        color: #1D1D1F !important;
        font-weight: 500 !important;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06) !important;
    }
    [data-testid="stButton"] > button:hover {
        border-color: rgba(0, 0, 0, 0.18) !important;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1) !important;
    }

    /* ── Alert banners ─────────────────────────────────────────────────── */
    [data-testid="stAlert"] {
        border-radius: 12px !important;
        border: 1px solid rgba(0, 0, 0, 0.06) !important;
    }

    /* ── Empty-state text ──────────────────────────────────────────────── */
    .empty-state {
        text-align: center;
        padding: 3rem 0;
        color: #86868B;
        font-size: 0.9rem;
    }
</style>
"""

# Apple system accent colours
_GREEN = "#34C759"
_RED   = "#FF3B30"
_BLUE  = "#007AFF"


def _kpi(col, label: str, value: str, color: str = "#1D1D1F", sub: str = "") -> None:
    """Render a KPI card with a custom value colour."""
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    col.markdown(
        f'<div class="kpi-card">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value" style="color:{color}">{value}</div>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _section(container, title: str, caption: str = "") -> None:
    """Render a section heading with blue left-accent bar."""
    cap_html = f'<div class="section-caption">{caption}</div>' if caption else ""
    container.markdown(
        f'<div class="section-heading">{title}</div>{cap_html}',
        unsafe_allow_html=True,
    )


_POSITION_COL_CONFIG = {
    "model_pct":      st.column_config.NumberColumn("Model %",      format="%.1f%%"),
    "bet_price_pct":  st.column_config.NumberColumn("Bet Price %",  format="%.1f%%"),
    "curr_price_pct": st.column_config.NumberColumn("Curr Price %", format="%.1f%%"),
    "price_chg_pct":  st.column_config.NumberColumn("Δ Price %",    format="%.1f%%"),
    "edge_pct":       st.column_config.NumberColumn("Edge %",       format="%.1f%%"),
    "kelly_pct":      st.column_config.NumberColumn("Kelly %",      format="%.2f%%"),
    "bet_usd":        st.column_config.NumberColumn("Bet ($)",       format="$%.2f"),
    "wtd_ev_usd":     st.column_config.NumberColumn("EV ($)",        format="$%.2f"),
}

_TRADES_COL_CONFIG = {
    "model_pct": st.column_config.NumberColumn("Model %",     format="%.1f%%"),
    "mkt_pct":   st.column_config.NumberColumn("Bet Price %", format="%.1f%%"),
    "edge_pct":  st.column_config.NumberColumn("Edge %",      format="%.1f%%"),
    "bet_usd":   st.column_config.NumberColumn("Bet ($)",      format="$%.2f"),
    "pnl_usd":   st.column_config.NumberColumn("P&L ($)",      format="$%.2f"),
    "slip_pct":  st.column_config.NumberColumn("Slip %",       format="%.3f%%"),
}


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

    # ── Sidebar (admin) ───────────────────────────────────────────
    with st.sidebar:
        st.markdown(
            "<div style='font-size:0.8rem;font-weight:600;color:#86868B;"
            "text-transform:uppercase;letter-spacing:0.08em;margin-bottom:12px'>"
            "Admin</div>",
            unsafe_allow_html=True,
        )
        _reset_pw = st.text_input(
            "Reset password", type="password", key="reset_pw",
            label_visibility="collapsed", placeholder="Reset password",
        )
        if _reset_pw == "91Carpentaria":
            if st.button("Reset all data", use_container_width=True):
                conn = sqlite3.connect(_get_db_path())
                c = conn.cursor()
                for tbl in ("results", "signals", "brier_scores", "backtest_runs"):
                    c.execute(f"DELETE FROM {tbl}")
                conn.commit()
                conn.close()
                st.rerun()
        elif _reset_pw:
            st.caption("Incorrect password.")

    # ── Page header with controls ──────────────────────────────────
    hdr, ctrl_tf, ctrl_refresh = st.columns([5, 1, 1])

    with ctrl_tf:
        st.markdown(
            "<div style='font-size:0.67rem;font-weight:600;color:#86868B;"
            "text-transform:uppercase;letter-spacing:0.09em;margin-bottom:4px'>"
            "Timeframe</div>",
            unsafe_allow_html=True,
        )
        tf = st.selectbox(
            "Timeframe", list(TIMEFRAMES.keys()), index=0,
            label_visibility="collapsed",
        )
        cutoff = _cutoff(tf)

    with ctrl_refresh:
        st.markdown(
            "<div style='font-size:0.67rem;font-weight:600;color:#86868B;"
            "text-transform:uppercase;letter-spacing:0.09em;margin-bottom:4px'>"
            "Auto-refresh</div>",
            unsafe_allow_html=True,
        )
        _refresh_label = st.selectbox(
            "Auto-refresh", list(_REFRESH_OPTIONS.keys()), index=1,
            label_visibility="collapsed",
        )
        refresh_interval = _REFRESH_OPTIONS[_refresh_label]

    with hdr:
        _refresh_note = (
            f"refreshes every {_refresh_label}" if refresh_interval else "auto-refresh off"
        )
        st.markdown(
            f'<div class="page-header">'
            f'<span class="page-title">WeatherEdge</span>'
            f'<span class="page-meta">'
            f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}  ·  {_refresh_note}'
            f'</span></div>',
            unsafe_allow_html=True,
        )

    st.divider()

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
        total_bet_usd = float(trades_raw["bet_size_usd"].sum())
        total_ev_usd = float((trades_raw["edge"] * trades_raw["bet_size_usd"]).sum())
        wtd_avg_edge = total_ev_usd / total_bet_usd if total_bet_usd > 0 else 0.0
    else:
        wins = losses = 0
        total_pnl = win_rate = avg_win = avg_loss = profit_factor = expectancy = avg_edge = 0.0
        total_ev_usd = wtd_avg_edge = 0.0
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
    tab_overview, tab_positions, tab_analysis, tab_history = st.tabs([
        "Overview", "Open Positions", "Analysis", "History",
    ])

    # =================================================================
    # TAB 1 — OVERVIEW
    # =================================================================
    with tab_overview:
        # ── KPI grid (two rows, no divider between them) ──────────
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        _kpi(c1, "Total P&L",
             f"${total_pnl:,.2f}" if n else "—",
             color=_GREEN if total_pnl > 0 else _RED if total_pnl < 0 else "#1D1D1F")
        _kpi(c2, "Win Rate",
             f"{win_rate:.1%}" if n else "—",
             color=_BLUE,
             sub=f"CI {win_ci[0]:.0%}–{win_ci[1]:.0%}" if n >= 5 else "")
        _kpi(c3, "Trades", str(n) if n else "—")
        _kpi(c4, "Wins / Losses",
             f"{wins} / {losses}" if n else "—",
             color=_GREEN if wins > losses else _RED if losses > wins else "#1D1D1F")
        _kpi(c5, "Avg Win",  f"${avg_win:,.2f}"  if wins   else "—", color=_GREEN if wins   else "#1D1D1F")
        _kpi(c6, "Avg Loss", f"${avg_loss:,.2f}" if losses else "—", color=_RED   if losses else "#1D1D1F")

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        c7, c8, c9, c10, c11, c12 = st.columns(6)
        _pf_val = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞" if n else "—"
        _kpi(c7, "Profit Factor", _pf_val,
             color=_GREEN if n and profit_factor > 1 else _RED if n and profit_factor <= 1 else "#1D1D1F")
        _kpi(c8, "Expectancy",
             f"${expectancy:,.2f}" if n else "—",
             color=_GREEN if expectancy > 0 else _RED if expectancy < 0 else "#1D1D1F")
        _kpi(c9, "Sharpe (ann.)",
             f"{sharpe:.2f}" if n > 1 else "—",
             color=_BLUE if sharpe > 0 else _RED if sharpe < 0 else "#1D1D1F")
        _kpi(c10, "Max Drawdown",
             f"{mdd:.1%}" if n > 1 else "—",
             color=_RED if mdd < -0.05 else "#1D1D1F")
        _kpi(c11, "Avg Edge",
             f"{avg_edge:.1%}" if n else "—",
             color=_BLUE if n else "#1D1D1F")
        _bss_color = _GREEN if (bss or 0) > 0 else _RED if (bss or 0) < 0 else "#1D1D1F"
        _kpi(c12, "Brier Skill",
             f"{bss:+.1%}" if bss is not None else "—",
             color=_bss_color)

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        d1, d2, _d3, _d4, _d5, _d6 = st.columns(6)
        _kpi(d1, "Wtd Avg Edge",
             f"{wtd_avg_edge:.1%}" if n else "—",
             color=_BLUE if n else "#1D1D1F",
             sub="size-weighted edge")
        _ev_color = _GREEN if total_ev_usd > 0 else _RED if total_ev_usd < 0 else "#1D1D1F"
        _kpi(d2, "Total Exp. EV",
             f"${total_ev_usd:,.2f}" if n else "—",
             color=_ev_color,
             sub="Σ edge × bet size")

        st.divider()

        # ── Charts ────────────────────────────────────────────────
        if len(equity_df) > 1:
            equity_df["resolved_at"] = pd.to_datetime(equity_df["resolved_at"], format="ISO8601")
            equity_idx = equity_df.set_index("resolved_at")

            col_eq, col_dd = st.columns(2)

            with col_eq:
                _section(st, "Equity Curve")
                st.line_chart(
                    equity_idx[["cumulative_pnl"]].rename(columns={"cumulative_pnl": "P&L ($)"}),
                    use_container_width=True,
                    color=_GREEN,
                )

            with col_dd:
                _section(st, "Drawdown")
                cum = equity_idx["cumulative_pnl"].values.astype(float)
                peak = np.maximum.accumulate(cum)
                dd_pct = np.where(peak > 0, (cum - peak) / peak * 100, 0.0)
                dd_df = pd.DataFrame({"Drawdown (%)": dd_pct}, index=equity_idx.index)
                st.line_chart(dd_df, use_container_width=True, color=_RED)

            # Rolling win rate
            if n >= 10:
                st.divider()
                _section(st, "Rolling Win Rate",
                         "30-trade rolling window — shows consistency of edge over time")
                rolling = trades_raw.copy()
                rolling["resolved_at"] = pd.to_datetime(rolling["resolved_at"], format="ISO8601")
                rolling = rolling.sort_values("resolved_at")
                rolling["Win Rate (%)"] = rolling["won"].rolling(30, min_periods=5).mean() * 100
                rolling = rolling.set_index("resolved_at").dropna(subset=["Win Rate (%)"])
                st.line_chart(rolling["Win Rate (%)"], use_container_width=True, color=_BLUE)
        else:
            st.markdown(
                '<div class="empty-state">Not enough resolved trades for charts yet.</div>',
                unsafe_allow_html=True,
            )

    # =================================================================
    # TAB 2 — OPEN POSITIONS
    # =================================================================
    with tab_positions:
        active_df = _query("""
            SELECT
                s.created_at as opened,
                m.station_id as station,
                m.metric,
                m.question as market,
                s.bin_index,
                m.outcomes,
                ROUND(s.model_prob * 100, 1) as model_pct,
                ROUND(s.market_prob * 100, 1) as bet_price_pct,
                ROUND(
                    (SELECT CAST(json_extract(mp.prices, '$.' || CAST(s.bin_index AS TEXT)) AS REAL)
                     FROM market_prices mp
                     WHERE mp.market_id = s.market_id
                     ORDER BY mp.recorded_at DESC
                     LIMIT 1) * 100,
                1) as curr_price_pct,
                ROUND(s.edge * 100, 1) as edge_pct,
                ROUND(s.kelly_fraction * 100, 2) as kelly_pct,
                ROUND(s.bet_size_usd, 2) as bet_usd,
                ROUND(s.edge * s.bet_size_usd, 2) as wtd_ev_usd,
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
            # Δ Price = how much the market has moved since we entered
            active_df["price_chg_pct"] = (
                active_df["curr_price_pct"] - active_df["bet_price_pct"]
            ).round(1)
            total_exposure = active_df["bet_usd"].sum()
            avg_pos_edge = active_df["edge_pct"].mean()
            portfolio_ev = active_df["wtd_ev_usd"].sum()

            p1, p2, p3, p4, *_ = st.columns(6)
            _kpi(p1, "Open Positions", str(len(active_df)), color=_BLUE)
            _kpi(p2, "Total Exposure", f"${total_exposure:,.2f}")
            _kpi(p3, "Avg Edge", f"{avg_pos_edge:.1f}%", color=_BLUE)
            _ev_col = _GREEN if portfolio_ev > 0 else _RED if portfolio_ev < 0 else "#1D1D1F"
            _kpi(p4, "Portfolio EV", f"${portfolio_ev:,.2f}", color=_ev_col,
                 sub="Σ edge × bet size")

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

            display_cols = ["opened", "station", "metric", "market", "bet_outcome",
                            "model_pct", "bet_price_pct", "curr_price_pct", "price_chg_pct",
                            "edge_pct", "kelly_pct", "bet_usd", "wtd_ev_usd", "end_date"]
            st.dataframe(
                active_df[[c for c in display_cols if c in active_df.columns]],
                use_container_width=True,
                hide_index=True,
                column_config=_POSITION_COL_CONFIG,
            )
        else:
            st.markdown(
                '<div class="empty-state">No open positions.</div>',
                unsafe_allow_html=True,
            )

    # =================================================================
    # TAB 3 — ANALYSIS
    # =================================================================
    with tab_analysis:
        if n == 0:
            st.markdown(
                '<div class="empty-state">No resolved trades in this window.</div>',
                unsafe_allow_html=True,
            )
        else:
            # ── Station breakdown ──────────────────────────────────
            _section(st, "Performance by Station")
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

            # ── Metric breakdown ───────────────────────────────────
            _section(st, "Performance by Weather Metric")
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

            # ── Calibration ────────────────────────────────────────
            _section(st, "Model Calibration",
                     "Predicted probability vs actual win rate — a well-calibrated model sits on the diagonal.")
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
                    st.caption(f"Expected Calibration Error (ECE): {ece:.3f}  ·  lower is better")
                    calib_chart = pd.DataFrame({
                        "model": [d["predicted_mean"] for d in diagram],
                        "actual": [d["actual_frequency"] for d in diagram],
                        "perfect": [d["predicted_mean"] for d in diagram],
                    }).set_index("model")
                    st.line_chart(calib_chart, use_container_width=True)
            else:
                st.caption("Need ≥10 resolved trades for calibration chart.")

            st.divider()

            # ── Signal distributions ───────────────────────────────
            _section(st, "Signal Distributions")
            col_d1, col_d2 = st.columns(2)

            with col_d1:
                st.caption("Bet size ($)")
                bet_vals = trades_raw["bet_size_usd"].dropna().values
                if len(bet_vals):
                    counts, bins = np.histogram(bet_vals, bins=10)
                    st.bar_chart(
                        pd.DataFrame({"count": counts},
                                     index=[f"${b:.0f}" for b in bins[:-1]]),
                        use_container_width=True,
                    )

            with col_d2:
                st.caption("Edge (%)")
                edge_vals = (trades_raw["edge"].dropna() * 100).values
                if len(edge_vals):
                    counts, bins = np.histogram(edge_vals, bins=10)
                    st.bar_chart(
                        pd.DataFrame({"count": counts},
                                     index=[f"{b:.1f}%" for b in bins[:-1]]),
                        use_container_width=True,
                    )

            st.divider()

            # ── Edge buckets ───────────────────────────────────────
            _section(st, "Edge Buckets — Predicted vs Actual",
                     "Does higher edge correlate with better outcomes?")
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
    # TAB 4 — HISTORY
    # =================================================================
    with tab_history:
        _section(st, "Recent Trades")
        recent_df = _query("""
            SELECT
                r.resolved_at as resolved,
                m.station_id as station,
                m.metric,
                m.question as market,
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
            display_cols = ["resolved", "station", "metric", "market", "bet_outcome",
                            "model_pct", "mkt_pct", "edge_pct", "bet_usd", "won", "pnl_usd", "slip_pct"]
            st.dataframe(
                recent_df[[c for c in display_cols if c in recent_df.columns]],
                use_container_width=True,
                hide_index=True,
                column_config=_TRADES_COL_CONFIG,
            )
        else:
            st.markdown(
                '<div class="empty-state">No resolved trades in this window.</div>',
                unsafe_allow_html=True,
            )

        st.divider()
        _section(st, "Brier Scores by Market")
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
            st.markdown(
                '<div class="empty-state">No Brier scores in this window.</div>',
                unsafe_allow_html=True,
            )

    # ── Auto-refresh ───────────────────────────────────────────────
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
