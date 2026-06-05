"""
Toxic-fill backtest — the honest go/no-go for market-making weather markets.

It answers: if you rested a passive two-sided quote (bid/ask at the edge of the
reward band) on a real weather market, how often would you get filled, and how
much would those fills cost you to *informed* flow (adverse selection)? Then it
nets that cost against the reward you'd earn, so you see the REAL number —
not the gross-reward mirage.

It is READ-ONLY (downloads the public pmxt archive; places no orders).

Method
------
1. Load the intraday mid-price path of a resolved token from the pmxt order-book
   archive (hourly Parquet snapshots).
2. Simulate a passive maker that re-quotes bid = mid-band, ask = mid+band each
   step. A side fills when the mid gaps THROUGH your quote — i.e. exactly when
   the market moved against you.
3. Score every fill two ways:
     - adverse_pnl:     mark to the NEXT mid (pure adverse-selection / toxicity;
                        ≤ 0 by construction — you only fill on adverse moves).
     - resolution_pnl:  mark to the final 0/1 outcome (if you got stuck holding).
4. Net against the reward you'd earn over the window:
       net = reward_over_window − |adverse cost|.

This is a FIRST-ORDER model: one fill per interval, no requoting latency, no
queue position, no hedging. It is meant to size the toxicity, not to be exact.

Run on the VPS (has bandwidth + reaches the archive). Example:
    python -m scripts.sim_maker --token-id 0x1234... --resolves 0 \
        --date 2026-05-29 --hours 12-23 --size 50 --band 0.045 \
        --reward-per-day 61 --share 0.10
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

log = logging.getLogger("sim_maker")


# ---------------------------------------------------------------------------
# Simulation core  (pure + unit-testable — no network)
# ---------------------------------------------------------------------------

def simulate_passive_maker(
    path: list[tuple[int, float]],
    band: float,
    size_usd: float,
    resolution_value: float,
) -> dict[str, float]:
    """
    Simulate a passive two-sided maker over a mid-price ``path`` of
    ``(timestamp_ms, mid)`` points sorted ascending.

    Each step we quote bid = mid-band, ask = mid+band. A side fills when the
    next mid gaps through it (the market came to us = adverse move).

    Returns fills, shares traded, and two P&L views (adverse + resolution).
    """
    fills = 0
    shares = 0.0
    adverse_pnl = 0.0      # mark to next mid; ≤ 0 — this is the toxicity
    resolution_pnl = 0.0   # mark to final 0/1 outcome

    for (_, m0), (_, m1) in zip(path, path[1:]):
        bid = m0 - band
        ask = m0 + band
        if m1 <= bid and bid > 0:
            # Filled on the bid: we BOUGHT the token at `bid`, now worth m1 < bid.
            sh = size_usd / bid
            fills += 1
            shares += sh
            adverse_pnl += (m1 - bid) * sh
            resolution_pnl += (resolution_value - bid) * sh
        elif m1 >= ask and ask < 1:
            # Filled on the ask: we SOLD the token at `ask`, now worth m1 > ask.
            sh = size_usd / ask
            fills += 1
            shares += sh
            adverse_pnl += (ask - m1) * sh
            resolution_pnl += (ask - resolution_value) * sh

    return {
        "fills": fills,
        "shares": shares,
        "adverse_pnl": adverse_pnl,
        "resolution_pnl": resolution_pnl,
    }


# ---------------------------------------------------------------------------
# pmxt loader  (defensive about the inner JSON schema)
# ---------------------------------------------------------------------------

def _mid_from_book(data: Any) -> float | None:
    """Extract a midpoint from a pmxt book `data` payload, tolerating shapes."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(data, dict):
        return None

    def _best(levels, want_max):
        prices = []
        for lvl in levels or []:
            if isinstance(lvl, dict) and "price" in lvl:
                prices.append(float(lvl["price"]))
            elif isinstance(lvl, (list, tuple)) and lvl:
                prices.append(float(lvl[0]))
        if not prices:
            return None
        return max(prices) if want_max else min(prices)

    # Shape A: explicit best bid/ask
    for bk, ak in (("best_bid", "best_ask"), ("bid", "ask")):
        if data.get(bk) is not None and data.get(ak) is not None:
            return (float(data[bk]) + float(data[ak])) / 2.0

    # Shape B: bid/ask level lists
    bb = _best(data.get("bids"), want_max=True)
    ba = _best(data.get("asks"), want_max=False)
    if bb is not None and ba is not None:
        return (bb + ba) / 2.0
    if bb is not None:
        return bb
    if ba is not None:
        return ba
    return None


def _download_to_temp(url: str, timeout: int = 240) -> str:
    """Stream a remote Parquet to a temp file using ~1 MB of RAM. Returns the path."""
    import os
    import tempfile
    import httpx

    fd, tmp = tempfile.mkstemp(suffix=".parquet")
    with os.fdopen(fd, "wb") as f:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
    return tmp


def load_price_paths(token_ids: list[str], day: date, hours: range) -> dict[str, list[tuple[int, float]]]:
    """
    Build intraday ``{token_id: [(ts_ms, mid), ...]}`` paths for several tokens
    at once. Each hourly pmxt Parquet (hundreds of MB) is downloaded ONCE and
    filtered for all tokens — never re-downloaded per token.

    Memory-safe for small (≈1 GB) hosts: the file is streamed to disk, then read
    with Polars' LAZY engine so the filter + column projection are pushed down and
    only the handful of matching rows ever materialise in RAM (a full read of
    these files OOM-kills a 1 GB droplet).
    """
    import os
    import polars as pl
    from weatheredge.config import PMXT_ARCHIVE_BASE
    from weatheredge.pmxt_fetcher import _normalize_id

    norm_to_orig = {_normalize_id(t): t for t in token_ids}
    norms = list(norm_to_orig.keys())
    paths: dict[str, list[tuple[int, float]]] = {t: [] for t in token_ids}

    for h in hours:
        url = f"{PMXT_ARCHIVE_BASE}/polymarket_orderbook_{day.isoformat()}T{h:02d}.parquet"
        try:
            tmp = _download_to_temp(url)
        except Exception as e:
            log.warning("hour %02d: download failed (%s)", h, str(e).splitlines()[0])
            continue
        try:
            sub = (
                pl.scan_parquet(tmp)
                .filter(
                    pl.col("market_id").is_in(norms)
                    & (pl.col("update_type") == "book_snapshot")
                )
                .select(
                    pl.col("market_id"),
                    pl.col("timestamp_received").cast(pl.Int64).alias("_ts"),
                    pl.col("data"),
                )
                .collect(streaming=True)
            )
        except Exception as e:
            log.warning("hour %02d: parse failed (%s)", h, str(e).splitlines()[0])
            os.unlink(tmp)
            continue
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        n = 0
        for row in sub.iter_rows(named=True):
            orig = norm_to_orig.get(row["market_id"])
            if orig is None:
                continue
            mid = _mid_from_book(row["data"])
            if mid is None or not (0.0 < mid < 1.0):
                continue
            paths[orig].append((int(row["_ts"]), mid))
            n += 1
        log.info("hour %02d: %d usable snapshots across %d token(s)", h, n, len(token_ids))

    for t in paths:
        paths[t].sort(key=lambda x: x[0])
    return paths


async def fetch_event_children(event_id: str) -> list[dict[str, Any]]:
    """
    For a resolved Polymarket event, return one entry per child bin:
    {token_id (YES), title, resolves (1 if that bin won else 0)}.
    """
    import json as _json
    from weatheredge.config import POLYMARKET_GAMMA_BASE
    from weatheredge.polymarket import _get_json

    data = await _get_json(f"{POLYMARKET_GAMMA_BASE}/events/{event_id}")
    children: list[dict[str, Any]] = []
    for m in (data.get("markets") or []):
        clob = m.get("clobTokenIds")
        if isinstance(clob, str):
            try:
                clob = _json.loads(clob)
            except (_json.JSONDecodeError, TypeError):
                clob = []
        if not clob:
            continue
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = _json.loads(prices)
            except (_json.JSONDecodeError, TypeError):
                prices = []
        resolves = 1 if (prices and float(prices[0]) > 0.9) else 0
        children.append({
            "token_id": str(clob[0]),
            "title": (m.get("groupItemTitle") or m.get("question") or "?")[:40],
            "resolves": resolves,
        })
    return children


def _parse_hours(spec: str) -> range:
    if "-" in spec:
        a, b = spec.split("-", 1)
        return range(int(a), int(b) + 1)
    h = int(spec)
    return range(h, h + 1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _report(label: str, path: list[tuple[int, float]], resolves: int, args) -> dict[str, float] | None:
    """Simulate one token's path and print its line; return its P&L dict."""
    if len(path) < 2:
        log.info("  %-40s  (only %d snapshots — skipped)", label, len(path))
        return None
    span_min = (path[-1][0] - path[0][0]) / 60_000
    res = simulate_passive_maker(path, args.band, args.size, float(resolves))
    window_days = max(span_min / (60 * 24), 1e-9)
    reward = args.reward_per_day * window_days * args.share
    net_adv = reward + res["adverse_pnl"]
    log.info(
        "  %-40s res=%d fills=%-4d adverse=%+7.2f$ resol=%+8.2f$ reward=%+6.2f$ net=%+7.2f$",
        label, resolves, res["fills"], res["adverse_pnl"], res["resolution_pnl"], reward, net_adv,
    )
    return {**res, "reward": reward, "net_adverse": net_adv}


def main(args) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    day = date.fromisoformat(args.date)
    hours = _parse_hours(args.hours)

    # Resolve the set of (token_id, resolves, label) to simulate.
    targets: list[tuple[str, int, str]] = []
    if args.event_id:
        import asyncio
        children = asyncio.run(fetch_event_children(args.event_id))
        if not children:
            log.info("No child markets found for event %s.", args.event_id)
            return
        targets = [(c["token_id"], c["resolves"], c["title"]) for c in children]
        log.info("Event %s: %d child bins (%d resolved YES)\n",
                 args.event_id, len(targets), sum(t[1] for t in targets))
    elif args.token_id:
        if args.resolves is None:
            log.info("--token-id requires --resolves 0|1")
            return
        targets = [(args.token_id, args.resolves, args.token_id[:18])]
    else:
        log.info("Provide either --event-id or (--token-id and --resolves).")
        return

    log.info("Loading pmxt books for %d token(s), %s hours %s (one download per hour)...",
             len(targets), day, args.hours)
    paths = load_price_paths([t[0] for t in targets], day, hours)

    log.info("\nPASSIVE MAKER  (size $%.0f/side, band %.3f, reward $%.0f/day × %.0f%% share):\n",
             args.size, args.band, args.reward_per_day, args.share * 100)

    agg_adverse = agg_resol = agg_reward = 0.0
    counted = 0
    for tid, resolves, label in targets:
        r = _report(label, paths.get(tid, []), resolves, args)
        if r:
            agg_adverse += r["adverse_pnl"]
            agg_resol += r["resolution_pnl"]
            agg_reward += r["reward"]
            counted += 1

    if not counted:
        log.info("\nNo token had enough snapshots. Check the date/hours have pmxt coverage, "
                 "or run weatheredge.pmxt_fetcher.probe_schema to confirm the schema.")
        return

    log.info("  " + "-" * 90)
    log.info("  TOTAL over %d bins:  adverse=%+.2f$  reward=%+.2f$  NET=%+.2f$   (hold-to-resol=%+.2f$)",
             counted, agg_adverse, agg_reward, agg_reward + agg_adverse, agg_resol)

    net = agg_reward + agg_adverse
    log.info(
        "\n%s",
        "→ Net POSITIVE here — worth widening the study to more events/days."
        if net > 0 else
        "→ Net NEGATIVE — toxic fills eat the reward. This is the expected result for\n"
        "  NAIVE quoting, and it's the whole point: the open question is whether your\n"
        "  weather model can cut the adverse fills (skew/pull quotes near releases)\n"
        "  enough to flip the sign. That, not raw rewards, is the real edge test.",
    )
    log.info(
        "\nFirst-order model (one fill/interval, no hedging/queue/latency). One event is an\n"
        "anecdote — run several resolved events/days before trusting the sign."
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Toxic-fill backtest for passive weather market-making.")
    ap.add_argument("--event-id", help="Resolved Polymarket event id (e.g. 536793) — simulates ALL its bins")
    ap.add_argument("--token-id", help="Single CLOB token id (alternative to --event-id; needs --resolves)")
    ap.add_argument("--resolves", type=int, choices=[0, 1], default=None, help="For --token-id: resolved YES(1)/NO(0)")
    ap.add_argument("--date", required=True, help="Resolution day YYYY-MM-DD (which day's archive to load)")
    ap.add_argument("--hours", default="12-23", help="UTC hour range to load, e.g. 12-23 (default)")
    ap.add_argument("--size", type=float, default=50.0, help="USD rested per side")
    ap.add_argument("--band", type=float, default=0.045, help="Quote offset from mid in price units (4.5c default)")
    ap.add_argument("--reward-per-day", type=float, default=0.0, help="This market's daily reward pool $ (from eval_rewards)")
    ap.add_argument("--share", type=float, default=0.0, help="Your reward share fraction (from eval_rewards, e.g. 0.10)")
    main(ap.parse_args())
