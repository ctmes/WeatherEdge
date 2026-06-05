"""
WebSocket Client Module - Real-time Market Data for WeatherEdge

Provides WebSocket connectivity for Polymarket CLOB API with
integrated StaleDataGuard as requested by the Council.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Union

from weatheredge.config import VERIFY_SSL

logger = logging.getLogger(__name__)

# WebSocket endpoints
WSS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _load_websockets():
    """Resolve WebSocket client functions without importing legacy APIs."""
    try:
        from websockets.asyncio.client import connect as ws_connect
        from websockets.exceptions import ConnectionClosed
        return ws_connect, ConnectionClosed
    except ImportError:
        try:
            import websockets
            return websockets.connect, websockets.exceptions.ConnectionClosed
        except ImportError:
            # FIX #9: use a dedicated sentinel exception rather than the
            # catch-all `Exception` so that unrelated errors are not silently
            # swallowed in the fallback path.
            class _WebsocketsNotInstalled(Exception):
                pass
            return None, _WebsocketsNotInstalled


@dataclass
class OrderbookLevel:
    """Single level in the orderbook."""
    price: float
    size: float


@dataclass
class TradeEvent:
    """Real-time trade execution event."""
    asset_id: str
    price: float
    size: float
    side: str  # "buy" or "sell"
    timestamp: int
    received_at: float = field(default_factory=time.time)


@dataclass
class OrderbookSnapshot:
    """Complete orderbook snapshot with VWAP support."""
    asset_id: str
    market: str
    timestamp: int
    bids: List[OrderbookLevel] = field(default_factory=list)
    asks: List[OrderbookLevel] = field(default_factory=list)
    hash: str = ""
    received_at: float = field(default_factory=time.time)

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2
        elif self.best_bid > 0:
            return self.best_bid
        elif self.best_ask > 0:
            return self.best_ask
        return 0.5

    def get_vwap(self, size_usd: float, side: str = "buy") -> float:
        """
        Calculate the volume-weighted average price to fill ``size_usd``
        worth of contracts by walking the book.

        FIX #1: the original denominator ``size_usd / self.mid_price``
        estimated total shares at mid price rather than tracking the
        actual shares filled at each level, producing a systematically
        wrong VWAP whenever the book was walked more than one level deep.
        We now accumulate ``total_shares`` directly in the walk loop.
        """
        levels = self.asks if side == "buy" else self.bids
        if not levels:
            return self.mid_price

        remaining_usd = size_usd
        total_cost = 0.0
        total_shares = 0.0  # FIX #1: track actual shares filled

        for level in levels:
            level_value_usd = level.size * level.price
            if remaining_usd <= level_value_usd:
                # Partially fill this level
                shares_here = remaining_usd / level.price
                total_cost += remaining_usd
                total_shares += shares_here
                remaining_usd = 0.0
                break
            else:
                # Fully consume this level
                total_cost += level_value_usd
                total_shares += level.size
                remaining_usd -= level_value_usd

        if total_shares == 0:
            return self.mid_price

        # If the book was exhausted before filling, return the worst level
        # price as a conservative estimate rather than a misleading average.
        if remaining_usd > 0:
            logger.warning(
                "get_vwap: book exhausted for asset %s; %.4f USD unfilled",
                self.asset_id,
                remaining_usd,
            )
            return levels[-1].price

        return total_cost / total_shares  # FIX #1: true VWAP

    @classmethod
    def from_message(cls, msg: Dict[str, Any]) -> "OrderbookSnapshot":
        bids = [
            OrderbookLevel(price=float(b["price"]), size=float(b["size"]))
            for b in msg.get("bids", [])
        ]
        asks = [
            OrderbookLevel(price=float(a["price"]), size=float(a["size"]))
            for a in msg.get("asks", [])
        ]
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        asset_id = msg.get("asset_id") or msg.get("market", "")
        return cls(
            asset_id=asset_id,
            market=msg.get("market", ""),
            timestamp=int(msg.get("timestamp", 0)),
            bids=bids,
            asks=asks,
            hash=msg.get("hash", ""),
            received_at=time.time(),
        )

    def apply_price_change(self, changes: List[Dict[str, Any]]) -> None:
        """
        FIX #7: apply incremental ``price_change`` level updates in-place
        rather than waiting for the next full ``book`` snapshot.

        Each change dict has ``price``, ``size``, and ``side`` (``"BUY"``
        or ``"SELL"``).  A size of ``"0"`` means the level was removed.
        """
        for change in changes:
            price = float(change["price"])
            size = float(change["size"])
            side = change.get("side", "").upper()
            levels: List[OrderbookLevel] = self.bids if side == "BUY" else self.asks

            # Find an existing level at this price
            idx = next((i for i, lvl in enumerate(levels) if lvl.price == price), None)

            if size == 0.0:
                # Remove the level
                if idx is not None:
                    levels.pop(idx)
            elif idx is not None:
                # Update existing level
                levels[idx].size = size
            else:
                # Insert new level
                levels.append(OrderbookLevel(price=price, size=size))

        # Re-sort after mutations
        self.bids.sort(key=lambda x: x.price, reverse=True)
        self.asks.sort(key=lambda x: x.price)
        self.received_at = time.time()


class StaleDataGuard:
    """Watchdog to invalidate signals if WebSocket data is lagged."""

    def __init__(self, max_age_seconds: float = 30.0, heartbeat_timeout: float = 10.0):
        self.max_age = max_age_seconds
        self.heartbeat_timeout = heartbeat_timeout
        # FIX #6: initialise to the current time so that is_fresh() does not
        # block all orderbooks before the first message is received.  The
        # heartbeat_timeout window still applies: if no message arrives within
        # that window after startup, the guard will correctly mark data as stale.
        self.last_message_at: float = time.time()

    def record_message(self) -> None:
        self.last_message_at = time.time()

    def is_fresh(self, snapshot_received_at: float) -> bool:
        now = time.time()
        # Connection-level check: no message for heartbeat_timeout → stale
        if now - self.last_message_at > self.heartbeat_timeout:
            return False
        # Snapshot-specific check: snapshot itself is too old → stale
        if now - snapshot_received_at > self.max_age:
            return False
        return True


class WeatherEdgeMarketWS:
    """Customized Polymarket WS client for WeatherEdge."""

    # Polymarket's book snapshot reply for a large subscription routinely exceeds
    # the `websockets` library default frame limit of 1 MiB (2**20). When that
    # happens the server sends a 1009 ("message too big") close and the client
    # tears down the connection — producing the infinite reconnect storm seen in
    # production where the orderbook cache was never populated. We raise the
    # limit well above any realistic snapshot size.
    DEFAULT_MAX_FRAME_BYTES = 32 * 2**20  # 32 MiB

    def __init__(
        self,
        url: str = WSS_MARKET_URL,
        reconnect_interval: float = 5.0,
        ping_interval: float = 20.0,
        max_size: int | None = DEFAULT_MAX_FRAME_BYTES,
    ):
        self.url = url
        self.reconnect_interval = reconnect_interval
        self.ping_interval = ping_interval
        self.max_size = max_size
        self._ws_connect, self._connection_closed = _load_websockets()

        self._ws = None
        self._running = False
        self._subscribed_assets: Set[str] = set()
        self._orderbooks: Dict[str, OrderbookSnapshot] = {}
        self.guard = StaleDataGuard()

        # Callbacks
        self.on_book_update: Optional[Callable[[OrderbookSnapshot], Any]] = None
        self.on_trade: Optional[Callable[[TradeEvent], Any]] = None

    async def connect(self) -> None:
        if self._ws_connect is None:
            raise RuntimeError("websockets is not installed")

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Origin": "https://polymarket.com",
        }
        kwargs: Dict[str, Any] = {
            "ping_interval": self.ping_interval,
            "additional_headers": headers,
            # Raise (or disable, when None) the inbound frame size limit so a
            # large book snapshot cannot trigger a 1009 close + reconnect loop.
            "max_size": self.max_size,
        }

        if self.url.startswith("wss://") and not VERIFY_SSL:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            kwargs["ssl"] = ssl_context
            # FIX #8: use %s-style logging throughout for lazy evaluation
            logger.info("Connecting to %s with SSL verification DISABLED", self.url)
        else:
            logger.info("Connecting to %s (Standard SSL)", self.url)

        self._ws = await self._ws_connect(self.url, **kwargs)
        logger.info("Connected to %s", self.url)

    async def subscribe(self, asset_ids: List[str]) -> None:
        if not asset_ids:
            return
        self._subscribed_assets.update(asset_ids)
        ws = self._ws
        if ws:
            # Polymarket's WS server closes the connection with 1009 if a
            # response frame exceeds ~1MB. Subscribing to hundreds of assets at
            # once triggers a large snapshot reply, so we chunk the list to
            # keep each response well under that limit.
            _CHUNK = 100
            ids = list(asset_ids)
            for i in range(0, len(ids), _CHUNK):
                chunk = ids[i:i + _CHUNK]
                msg = {"type": "subscribe", "assets_ids": chunk}
                await ws.send(json.dumps(msg))
                await asyncio.sleep(0.1)  # brief pause between chunks
            logger.info("Subscribed to %d assets", len(asset_ids))

    async def _invoke_callback(
        self,
        callback: Optional[Callable],
        payload: Any,
        label: str,
    ) -> None:
        """
        FIX #5: invoke a user callback, catching and logging any exception so
        that a misbehaving callback cannot crash the receive loop.
        """
        if callback is None:
            return
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(payload)
            else:
                callback(payload)
        except Exception:
            logger.exception("Exception in %s callback", label)

    async def _handle_message(self, data: Union[Dict, List]) -> None:
        self.guard.record_message()

        if isinstance(data, list):
            for item in data:
                await self._handle_message(item)
            return

        event_type = data.get("event_type")
        # Log keys for debugging field name changes in Polymarket API
        logger.debug("WS message: event_type=%s, keys=%s", event_type, list(data.keys()))

        if event_type == "book":
            snapshot = OrderbookSnapshot.from_message(data)
            # Store under both decimal ID and hex address to ensure price_change matches
            self._orderbooks[snapshot.asset_id] = snapshot
            if snapshot.market and snapshot.market != snapshot.asset_id:
                self._orderbooks[snapshot.market] = snapshot
            
            logger.debug("Book snapshot for asset %s (market %s)", snapshot.asset_id, snapshot.market)
            await self._invoke_callback(self.on_book_update, snapshot, "on_book_update")

        elif event_type == "price_change":
            # FIX #7: apply incremental level updates in-place so the book
            # stays accurate between full snapshots.
            asset_id = data.get("asset_id") or data.get("market", "")
            changes = data.get("changes", [])
            ob = self._orderbooks.get(asset_id)
            if ob is not None and changes:
                ob.apply_price_change(changes)
                logger.debug(
                    "price_change applied to asset %s (%d levels)", asset_id, len(changes)
                )
                await self._invoke_callback(self.on_book_update, ob, "on_book_update")
            else:
                logger.debug(
                    "price_change for unknown asset %s — no snapshot cached yet. Message keys: %s",
                    asset_id, list(data.keys())
                )

        elif event_type == "trade":
            trade = TradeEvent(
                asset_id=data.get("asset_id", ""),
                price=float(data.get("price", 0)),
                size=float(data.get("size", 0)),
                side=data.get("side", "unknown"),
                timestamp=int(data.get("timestamp", 0)),
            )
            await self._invoke_callback(self.on_trade, trade, "on_trade")

        else:
            logger.debug("Unhandled event_type=%r", event_type)

    async def _cleanup_ws(self) -> None:
        """
        FIX #4: close and discard a potentially broken WebSocket connection
        before a reconnect attempt so that stale state does not bleed through.
        """
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass  # Already broken; ignore close errors

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.connect()
                if self._subscribed_assets:
                    await self.subscribe(list(self._subscribed_assets))

                while self._running:
                    msg = await self._ws.recv()
                    if not msg or not msg.strip():
                        continue
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.debug("Skipping non-JSON message: %r", msg)
                        continue
                    await self._handle_message(data)

            # FIX #3: catch ConnectionClosed specifically for clean reconnect
            # logic, and log other unexpected exceptions at a higher severity.
            except self._connection_closed as e:
                logger.warning("WebSocket connection closed: %s", e)
            except Exception as e:
                logger.error("Unexpected WS error: %s", e, exc_info=True)
            finally:
                # FIX #4: always clean up before sleeping / retrying
                await self._cleanup_ws()
                if self._running:
                    logger.info(
                        "Reconnecting in %.1f seconds...", self.reconnect_interval
                    )
                    await asyncio.sleep(self.reconnect_interval)

    def get_valid_orderbook(self, asset_id: str) -> Optional[OrderbookSnapshot]:
        """Get orderbook ONLY if it passes the StaleDataGuard."""
        ob = self._orderbooks.get(asset_id)
        if ob and self.guard.is_fresh(ob.received_at):
            return ob
        return None

    async def stop(self) -> None:
        """
        FIX #2: `stop` is now an async method so the WebSocket close
        can be awaited safely from within the running event loop, avoiding
        the unsafe `asyncio.create_task(self._ws.close())` pattern that
        could raise if called from a sync context or after loop teardown.
        """
        self._running = False
        await self._cleanup_ws()
        logger.info("WeatherEdgeMarketWS stopped")