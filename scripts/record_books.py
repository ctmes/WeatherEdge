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
OUT_DIR = Path("data/book_recordings")


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

    buf: list[dict] = []
    last_at: dict[str, float] = {}

    def on_book(snap) -> None:
        if not isinstance(snap, OrderbookSnapshot):
            return
        mid = snap.mid_price
        if not (0.0 < mid < 1.0):
            return
        now = time.time()
        if now - last_at.get(snap.asset_id, 0.0) < args.min_interval:
            return
        last_at[snap.asset_id] = now
        buf.append({
            "ts": int(now * 1000),
            "token": snap.asset_id,
            "bid": snap.best_bid,
            "ask": snap.best_ask,
            "mid": mid,
        })

    ws = WeatherEdgeMarketWS()
    ws.on_book_update = on_book
    await ws.subscribe([r["token_id"] for r in targets])

    def _flush() -> int:
        if not buf:
            return 0
        chunk, buf[:] = buf[:], []
        day = _utcnow().strftime("%Y-%m-%d")
        with (OUT_DIR / f"books_{day}.jsonl").open("a") as f:
            for row in chunk:
                f.write(json.dumps(row) + "\n")
        return len(chunk)

    async def flusher() -> None:
        total = 0
        while True:
            await asyncio.sleep(args.flush)
            n = _flush()
            if n:
                total += n
                log.info("flushed %d snapshots (session total %d)", n, total)

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
        await asyncio.gather(ws.run(), flusher(), rediscover())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await ws.stop()
        n = _flush()
        log.info("Stopped. Final flush: %d snapshots.", n)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Record live weather-market order books for the toxic-fill backtest.")
    ap.add_argument("--top", type=int, default=12, help="Number of fattest reward pools to record")
    ap.add_argument("--min-interval", type=float, default=5.0, help="Min seconds between snapshots per token")
    ap.add_argument("--flush", type=float, default=30.0, help="Seconds between disk flushes")
    ap.add_argument("--rediscover", type=float, default=6.0, help="Hours between re-discovering fresh markets")
    asyncio.run(main(ap.parse_args()))
