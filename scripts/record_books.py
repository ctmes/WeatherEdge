"""
Live order-book recorder — collects real weather-market book snapshots so we can
run the toxic-fill backtest (sim_maker) on FRESH data, sidestepping the dead
pmxt archive.

Uses the (now 1009-fixed) WeatherEdgeMarketWS. It is read-only on the market —
it subscribes and listens; it never places an order.

What it writes (under data/book_recordings/):
  - markets_meta.jsonl : one line per recorded token (question, condition_id,
                         end_date, reward params) — needed later to resolve
                         outcomes and know the reward pool.
  - books_YYYY-MM-DD.jsonl : append-only snapshots {ts, token, bid, ask, mid}.

It re-discovers fresh weather markets periodically, because daily weather
markets resolve and roll over — a fixed subscription would go silent after a day.

Run it under tmux on the VPS for 1–2 weeks:
    tmux new -s recorder
    python -m scripts.record_books --top 12 --min-interval 5
    # detach: Ctrl-b then d

Then, once those markets have resolved, run sim_maker with --from-records
(see scripts/sim_maker.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.eval_rewards import (
    _fetch_sampling_markets, _is_weather, _parse_rewards, _first_token_id,
)
from weatheredge.ws_client import WeatherEdgeMarketWS, OrderbookSnapshot

log = logging.getLogger("record_books")
# Anchor to the repo root so the output folder is identical no matter which
# directory the recorder is launched from. A relative path silently split the
# data across ~/data and ~/WeatherEdge/data when started from different CWDs.
_REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = _REPO_ROOT / "data" / "book_recordings"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def discover_weather_tokens(top: int) -> list[dict]:
    """Return the top-`top` actively-rewarded weather markets (by daily pool)."""
    sampling = await _fetch_sampling_markets()
    rows: list[dict] = []
    for m in sampling:
        if not _is_weather(m):
            continue
        p = _parse_rewards(m)
        if not p or p["daily_rate"] <= 0:
            continue
        tid = _first_token_id(m)
        if not tid:
            continue
        rows.append({
            "token_id": tid,
            "question": (m.get("question") or "")[:120],
            "condition_id": m.get("condition_id") or m.get("question_id") or "",
            "end_date": m.get("end_date_iso") or m.get("end_date") or "",
            "reward_per_day": p["daily_rate"],
            "min_size": p["min_size"],
            "max_spread": p["max_spread"],
        })
    rows.sort(key=lambda r: r["reward_per_day"], reverse=True)
    return rows[:top]


async def main(args) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = OUT_DIR / "markets_meta.jsonl"
    known: set[str] = set()

    targets = await discover_weather_tokens(args.top)
    if not targets:
        log.error("No actively-rewarded weather markets found — nothing to record.")
        return
    with meta_path.open("a") as f:
        for r in targets:
            known.add(r["token_id"])
            f.write(json.dumps({**r, "discovered_at": _utcnow().isoformat()}) + "\n")
    log.info("Recording %d weather tokens (top pools: %s)",
             len(targets), ", ".join(f"${r['reward_per_day']:.0f}" for r in targets[:5]))

    books_buf: list[dict] = []   # full-depth book snapshots
    trades_buf: list[dict] = []  # individual trade prints (if the feed emits them)
    last_at: dict[str, float] = {}

    def _book_row(snap: OrderbookSnapshot, now_ms: int, src: str) -> dict:
        """Full-depth snapshot row — enough to reconstruct the book for any backtest."""
        n = args.depth_levels
        return {
            "ts": now_ms,
            "token": snap.asset_id,
            "bid": snap.best_bid,
            "ask": snap.best_ask,
            "mid": snap.mid_price,
            "bids": [[lvl.price, lvl.size] for lvl in snap.bids[:n]],
            "asks": [[lvl.price, lvl.size] for lvl in snap.asks[:n]],
            "src": src,
        }

    def on_book(snap) -> None:
        if not isinstance(snap, OrderbookSnapshot):
            return
        if not (0.0 < snap.mid_price < 1.0):
            return
        now = time.time()
        # Throttle event-driven captures per token (the heartbeat sampler covers gaps).
        if now - last_at.get(snap.asset_id, 0.0) < args.min_interval:
            return
        last_at[snap.asset_id] = now
        books_buf.append(_book_row(snap, int(now * 1000), "event"))

    def on_trade(tr) -> None:
        # Real trade prints upgrade the backtest from inferring fills to observing
        # them. NOTE: Polymarket's market channel may not emit these — if trades_
        # files stay empty, the feed simply doesn't carry trades on this channel.
        try:
            trades_buf.append({
                "ts": int(time.time() * 1000),
                "token": tr.asset_id,
                "price": tr.price,
                "size": tr.size,
                "side": tr.side,
            })
        except Exception:
            pass

    ws = WeatherEdgeMarketWS()
    ws.on_book_update = on_book
    ws.on_trade = on_trade
    await ws.subscribe([r["token_id"] for r in targets])

    def _flush_buf(buf: list[dict], prefix: str) -> int:
        if not buf:
            return 0
        chunk, buf[:] = buf[:], []
        day = _utcnow().strftime("%Y-%m-%d")
        with (OUT_DIR / f"{prefix}_{day}.jsonl").open("a") as f:
            for row in chunk:
                f.write(json.dumps(row) + "\n")
        return len(chunk)

    def _flush() -> tuple[int, int]:
        return _flush_buf(books_buf, "books"), _flush_buf(trades_buf, "trades")

    async def flusher() -> None:
        tot_b = tot_t = 0
        while True:
            await asyncio.sleep(args.flush)
            nb, nt = _flush()
            if nb or nt:
                tot_b += nb
                tot_t += nt
                log.info("flushed %d books, %d trades (session %d / %d)", nb, nt, tot_b, tot_t)

    async def sampler() -> None:
        # Heartbeat: snapshot each tracked token's full book on a fixed timer, even
        # when it hasn't ticked. Weather books are quiet, so event-driven capture
        # alone leaves gaps; this guarantees a regular, reconstructable series.
        while True:
            await asyncio.sleep(args.sample_interval)
            now_ms = int(time.time() * 1000)
            for tid in list(known):
                ob = ws._orderbooks.get(tid)
                if ob is None or not (0.0 < ob.mid_price < 1.0):
                    continue
                books_buf.append(_book_row(ob, now_ms, "poll"))

    async def rediscover() -> None:
        while True:
            await asyncio.sleep(args.rediscover * 3600)
            try:
                fresh = await discover_weather_tokens(args.top)
                new = [r for r in fresh if r["token_id"] not in known]
                if new:
                    await ws.subscribe([r["token_id"] for r in new])
                    with meta_path.open("a") as f:
                        for r in new:
                            known.add(r["token_id"])
                            f.write(json.dumps({**r, "discovered_at": _utcnow().isoformat()}) + "\n")
                    log.info("re-discovered %d new weather tokens (now tracking %d)", len(new), len(known))
            except Exception as e:
                log.warning("re-discovery failed: %s", e)

    log.info("Recording... (Ctrl-C to stop; data in %s/)", OUT_DIR)
    try:
        await asyncio.gather(ws.run(), flusher(), sampler(), rediscover())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await ws.stop()
        nb, nt = _flush()
        log.info("Stopped. Final flush: %d books, %d trades.", nb, nt)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Record live weather-market order books for the toxic-fill backtest.")
    ap.add_argument("--top", type=int, default=12, help="Number of fattest reward pools to record")
    ap.add_argument("--min-interval", type=float, default=5.0, help="Min seconds between event-driven snapshots per token")
    ap.add_argument("--sample-interval", type=float, default=60.0, help="Seconds between fixed-timer book snapshots per token (gap-free series)")
    ap.add_argument("--depth-levels", type=int, default=20, help="Order-book levels per side to record")
    ap.add_argument("--flush", type=float, default=30.0, help="Seconds between disk flushes")
    ap.add_argument("--rediscover", type=float, default=6.0, help="Hours between re-discovering fresh markets")
    asyncio.run(main(ap.parse_args()))
