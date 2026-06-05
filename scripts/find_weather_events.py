"""
Find resolved weather events that ended on a given date — so we can point
sim_maker at a market that BOTH resolved and has pmxt order-book coverage.

READ-ONLY (Gamma API only).

    python -m scripts.find_weather_events --date 2026-04-15

Prints, for each weather event ending that day: event id, title, #bins, the
winning bin, and a ready-to-paste sim_maker command.
"""

from __future__ import annotations

import argparse
import asyncio
import json as _json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

from weatheredge.config import POLYMARKET_GAMMA_BASE
from weatheredge.polymarket import _get_json

log = logging.getLogger("find_weather_events")

_WEATHER_RE = re.compile(r"\b(temperature|°\s*[cf]|celsius|fahrenheit)\b", re.IGNORECASE)


async def _events_for_window(target: date) -> list[dict[str, Any]]:
    """
    Fetch closed events ending on `target`. Tries Gamma date-window params first;
    falls back to paginating closed events ordered by endDate and filtering locally.
    """
    lo = datetime(target.year, target.month, target.day).isoformat() + "Z"
    hi = (datetime(target.year, target.month, target.day) + timedelta(days=1)).isoformat() + "Z"

    # Attempt 1: server-side date window
    try:
        data = await _get_json(f"{POLYMARKET_GAMMA_BASE}/events", {
            "closed": "true", "limit": "500",
            "end_date_min": lo, "end_date_max": hi,
            "order": "endDate", "ascending": "false",
        })
        if isinstance(data, list) and data:
            return data
    except Exception as e:
        log.debug("date-window query failed: %s", e)

    # Attempt 2: paginate closed events newest-first, keep those ending on target
    out: list[dict[str, Any]] = []
    offset = 0
    for _ in range(60):  # up to 6000 events
        try:
            data = await _get_json(f"{POLYMARKET_GAMMA_BASE}/events", {
                "closed": "true", "limit": "100", "offset": str(offset),
                "order": "endDate", "ascending": "false",
            })
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        stop = False
        for ev in data:
            ed = (ev.get("endDate") or "")[:10]
            if ed == target.isoformat():
                out.append(ev)
            elif ed and ed < target.isoformat():
                stop = True  # gone past the target going backwards
        if stop:
            break
        offset += 100
    return out


def _winning_bin(ev: dict[str, Any]) -> tuple[int, str, list[dict]]:
    """Return (winning_index, winning_title, children) for an event."""
    children = []
    for m in (ev.get("markets") or []):
        clob = m.get("clobTokenIds")
        if isinstance(clob, str):
            try:
                clob = _json.loads(clob)
            except (_json.JSONDecodeError, TypeError):
                clob = []
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = _json.loads(prices)
            except (_json.JSONDecodeError, TypeError):
                prices = []
        resolves = 1 if (prices and float(prices[0]) > 0.9) else 0
        children.append({
            "title": (m.get("groupItemTitle") or "?"),
            "resolves": resolves,
            "has_token": bool(clob),
        })
    win_i, win_t = -1, "?"
    for i, c in enumerate(children):
        if c["resolves"] == 1:
            win_i, win_t = i, c["title"]
            break
    return win_i, win_t, children


async def main(target: date, reward_per_day: float, share: float) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    events = await _events_for_window(target)
    weather = [e for e in events if _WEATHER_RE.search(e.get("title", ""))]
    log.info("Closed events ending %s: %d total, %d weather\n", target, len(events), len(weather))

    if not weather:
        log.info("None found. Try a nearby covered date (archive has 2026-03-03 and 2026-04-15).")
        return

    shown = 0
    for ev in weather:
        eid = ev.get("id")
        win_i, win_t, children = _winning_bin(ev)
        if win_i < 0 or not children:
            continue
        shown += 1
        log.info("event %s  |  %s", eid, (ev.get("title") or "")[:60])
        log.info("   %d bins, winner: bin %d = %s", len(children), win_i, win_t)
        log.info(
            "   python -m scripts.sim_maker --event-id %s --date %s --hours 14-20 "
            "--size 50 --band 0.045 --reward-per-day %.0f --share %.2f\n",
            eid, target.isoformat(), reward_per_day, share,
        )
        if shown >= 8:
            break

    if not shown:
        log.info("Weather events found but none had a clear resolved winner — try another date.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Find resolved weather events on a date (for sim_maker).")
    ap.add_argument("--date", default="2026-04-15", help="Target end date YYYY-MM-DD (must have pmxt coverage)")
    ap.add_argument("--reward-per-day", type=float, default=58.0, help="Placeholder reward $/day for the emitted command")
    ap.add_argument("--share", type=float, default=0.10, help="Placeholder reward share for the emitted command")
    args = ap.parse_args()
    asyncio.run(main(date.fromisoformat(args.date), args.reward_per_day, args.share))
