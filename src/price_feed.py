#!/usr/bin/env python3
"""
Gabagool Bot - Real-Time Price Feed (Binance Futures aggTrade WebSocket)

Purpose:
    Streams real-time BTC/ETH/SOL prices from Binance Futures aggTrade
    WebSocket. Provides rolling momentum calculation for the spike detector.

    WHY Binance Futures (fstream) over Coinbase:
      - Binance Futures is the highest-volume, lowest-latency crypto feed
      - aggTrade stream fires on every aggregated trade, not just ticks
      - This is the same feed Gabagool22 uses as his "crystal ball"
      - Polymarket resolves against Binance spot index — same source = no basis risk

    Stream URLs:
        BTC: wss://fstream.binance.com/ws/btcusdt@aggTrade
        ETH: wss://fstream.binance.com/ws/ethusdt@aggTrade
        SOL: wss://fstream.binance.com/ws/solusdt@aggTrade

    aggTrade message format:
        {
            "e": "aggTrade",   # event type
            "E": 1714000000000, # event time (ms)
            "s": "BTCUSDT",    # symbol
            "p": "80350.10",   # price
            "q": "0.012",      # quantity
            "T": 1714000000000, # trade time (ms)
            "m": false         # is buyer market maker?
        }

Author: AI-Generated
Created: 2026-05-03
Modified: 2026-05-04 — Switched source from Coinbase to Binance Futures

Dependencies:
    - websockets
    - asyncio

Usage:
    feed = BinancePriceFeed("BTC")
    asyncio.create_task(feed.connect_and_stream())

    momentum = feed.get_momentum_pct(seconds=5)
    price = feed.get_current_price()

Notes:
    - No authentication required (public stream)
    - Reconnects automatically on disconnect with exponential backoff
    - Stores last 5 minutes of price ticks
    - aggTrade fires on every trade — much higher resolution than Coinbase ticker
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict

try:
    import websockets
except ImportError:
    websockets = None


# ============================================================================
# Binance Futures aggTrade Configuration
# ============================================================================

# Binance Spot WebSocket (globally accessible, same aggTrade data)
# Note: Binance Futures (fstream) is geo-blocked in the US.
# Spot aggTrade is the same price feed — Polymarket resolves on Binance index.
BINANCE_FUTURES_WS = "wss://stream.binance.com:9443/ws"

# Symbol mapping: our asset → Binance symbol → stream name
BINANCE_SYMBOLS = {
    "BTC": ("BTCUSDT",  "btcusdt@aggTrade"),
    "ETH": ("ETHUSDT",  "ethusdt@aggTrade"),
    "SOL": ("SOLUSDT",  "solusdt@aggTrade"),
    "XRP": ("XRPUSDT",  "xrpusdt@aggTrade"),
}

# Rolling price history window (seconds)
PRICE_HISTORY_SECONDS = 300   # 5 minutes


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class PriceTick:
    """A single price observation from an aggTrade event."""
    timestamp: float   # unix time (seconds, float precision)
    price: float       # USD price


# ============================================================================
# BinancePriceFeed
# ============================================================================

class BinancePriceFeed:
    """
    Real-time price feed for a single asset via Binance Futures aggTrade WebSocket.

    Maintains a rolling 5-minute price history and provides momentum
    calculations used by the SpikeDetector.

    Key differences vs Coinbase ticker:
      - aggTrade fires on EVERY trade (sub-100ms resolution at peak)
      - Coinbase ticker fires once per second at best
      - Binance Futures prices are the canonical reference for Polymarket resolution
    """

    def __init__(self, asset: str = "BTC", max_history_seconds: int = PRICE_HISTORY_SECONDS):
        asset = asset.upper()
        if asset not in BINANCE_SYMBOLS:
            raise ValueError(f"Unsupported asset: {asset}. Supported: {list(BINANCE_SYMBOLS.keys())}")

        self.asset   = asset
        binance_sym, stream = BINANCE_SYMBOLS[asset]
        self.symbol  = binance_sym
        self.stream  = stream
        self.ws_url  = f"{BINANCE_FUTURES_WS}/{stream}"

        self.max_history_seconds = max_history_seconds
        self.logger = logging.getLogger(f"price_feed.{asset}")

        self._ticks: deque = deque()
        self._current_price: Optional[float] = None

        self.running = False
        self._data_event = asyncio.Event()

        # Event-driven spike callbacks.
        # Registered functions are called synchronously on EVERY tick
        # inside the WebSocket message handler — zero polling delay.
        self._on_tick_callbacks: list = []

    def register_on_tick(self, callback) -> None:
        """
        Register a callback to be invoked on every aggTrade tick.

        The callback receives (self,) — the price feed instance —
        so it can read get_current_price() and get_momentum_pct().

        MUST be non-blocking (synchronous). If it needs to trigger
        async work (like a burst snipe), it should set an asyncio.Event.
        """
        self._on_tick_callbacks.append(callback)

    # -------------------------------------------------------------------------
    # Public Interface (unchanged from Coinbase version)
    # -------------------------------------------------------------------------

    def get_current_price(self) -> Optional[float]:
        """Latest trade price in USD."""
        return self._current_price

    def get_momentum_pct(self, seconds: int = 5) -> Optional[float]:
        """
        % change from the price N seconds ago to now.
        Positive = price rose. Negative = price fell.

        Used by SpikeDetector with seconds=5 (5s momentum = latency arb signal).
        """
        now    = time.time()
        cutoff = now - seconds

        reference_price = None
        for tick in self._ticks:
            if tick.timestamp >= cutoff:
                reference_price = tick.price
                break

        if reference_price is None or self._current_price is None or reference_price == 0:
            return None

        return (self._current_price - reference_price) / reference_price * 100

    def get_price_at(self, seconds_ago: float) -> Optional[float]:
        """Price closest to N seconds ago."""
        target_time = time.time() - seconds_ago
        best_tick   = None
        best_diff   = float("inf")

        for tick in self._ticks:
            diff = abs(tick.timestamp - target_time)
            if diff < best_diff:
                best_diff = diff
                best_tick = tick

        return best_tick.price if best_tick else None

    def get_tick_count(self) -> int:
        return len(self._ticks)

    def has_data(self) -> bool:
        return self._current_price is not None

    async def wait_for_data(self, timeout: float = 15.0) -> bool:
        try:
            await asyncio.wait_for(self._data_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self.logger.warning("Timed out waiting for %s price data", self.asset)
            return False

    def stop(self) -> None:
        self.running = False

    # -------------------------------------------------------------------------
    # WebSocket Connection
    # -------------------------------------------------------------------------

    async def connect_and_stream(self) -> None:
        """
        Connect to Binance Futures aggTrade stream and process messages forever.
        Auto-reconnects with exponential backoff on disconnect.
        """
        if websockets is None:
            self.logger.error("websockets not installed: pip install websockets")
            return

        self.running = True
        self.logger.info(
            "Starting %s price feed from Binance Futures (%s)",
            self.asset, self.stream
        )

        backoff = 1.0
        while self.running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self.logger.info("%s WebSocket connected | %s", self.asset, self.ws_url)
                    backoff = 1.0  # reset on successful connect

                    # Purge stale ticks accumulated during the disconnect window.
                    # Pre-reconnect prices compared against post-reconnect prices
                    # would produce massive artificial momentum and trigger false
                    # positive snipes.
                    self._ticks.clear()
                    self._current_price = None
                    self._data_event.clear()

                    async for raw_msg in ws:
                        if not self.running:
                            break
                        self._handle_message(raw_msg)

            except asyncio.CancelledError:
                self.logger.info("%s price feed cancelled", self.asset)
                break
            except Exception as e:
                if self.running:
                    self.logger.warning(
                        "%s WebSocket disconnected (%s), reconnecting in %.0fs...",
                        self.asset, e, backoff
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)  # cap at 30s

        self.running = False
        self.logger.info("%s price feed stopped", self.asset)

    def _handle_message(self, raw_msg: str) -> None:
        """
        Parse a Binance aggTrade message and store the price tick.

        Binance aggTrade format:
            {
                "e": "aggTrade",
                "E": 1714000000000,   <- event time ms
                "s": "BTCUSDT",
                "p": "80350.10",      <- price  ← the one we want
                "q": "0.012",         <- quantity
                "T": 1714000000000,   <- trade time ms
                "m": false
            }
        """
        try:
            data = json.loads(raw_msg)

            # Validate event type — only process aggTrade events
            if data.get("e") != "aggTrade":
                return

            price_str = data.get("p")
            if not price_str:
                return

            price = float(price_str)
            if price <= 0:
                return

            # Binance provides trade time in milliseconds ("T" field)
            trade_time_ms = data.get("T") or data.get("E")
            timestamp = trade_time_ms / 1000.0 if trade_time_ms else time.time()

            tick = PriceTick(timestamp=timestamp, price=price)

            self._current_price = price
            self._ticks.append(tick)

            # Prune ticks older than history window
            cutoff = timestamp - self.max_history_seconds
            while self._ticks and self._ticks[0].timestamp < cutoff:
                self._ticks.popleft()

            if not self._data_event.is_set():
                self._data_event.set()
                self.logger.info(
                    "%s first price received: $%.2f (Binance aggTrade)",
                    self.asset, price
                )

            # Fire registered tick callbacks (event-driven spike detection).
            # This is called on EVERY aggTrade message — typically every 10-50ms
            # at peak BTC volume. Callbacks must be non-blocking.
            for cb in self._on_tick_callbacks:
                try:
                    cb(self)
                except Exception as cb_err:
                    self.logger.debug("Tick callback error: %s", cb_err)

        except Exception as e:
            self.logger.debug("Error parsing aggTrade message: %s", e)

    def get_summary(self) -> Dict:
        """Current feed state summary."""
        return {
            "asset":        self.asset,
            "source":       f"Binance Futures ({self.stream})",
            "current_price": self._current_price,
            "tick_count":   len(self._ticks),
            "momentum_5s":  self.get_momentum_pct(5),
            "momentum_30s": self.get_momentum_pct(30),
            "has_data":     self.has_data(),
            "running":      self.running,
        }


# ============================================================================
# PriceFeedManager
# ============================================================================

class PriceFeedManager:
    """
    Manages multiple Binance Futures price feeds simultaneously.

    Example:
        manager = PriceFeedManager(["BTC", "ETH", "SOL"])
        await manager.start()

        momentum = manager.get_momentum("BTC", seconds=5)
        price    = manager.get_price("BTC")
    """

    def __init__(self, assets: list = None):
        self.assets = [a.upper() for a in (assets or ["BTC", "ETH", "SOL"])]
        self.feeds:  Dict[str, BinancePriceFeed] = {}
        self.tasks:  Dict[str, asyncio.Task] = {}
        self.logger = logging.getLogger("price_feed_manager")

        for asset in self.assets:
            try:
                self.feeds[asset] = BinancePriceFeed(asset)
            except ValueError as e:
                self.logger.warning("Skipping unsupported asset: %s", e)

    async def start(self) -> None:
        """Start all Binance aggTrade WebSocket connections."""
        for asset, feed in self.feeds.items():
            task = asyncio.create_task(
                feed.connect_and_stream(),
                name=f"price_feed_{asset}"
            )
            self.tasks[asset] = task
            self.logger.info("Started Binance aggTrade feed for %s", asset)

    async def wait_for_all_feeds(self, timeout: float = 15.0) -> bool:
        """Wait until all feeds have received at least one price tick."""
        tasks = [
            feed.wait_for_data(timeout=timeout)
            for feed in self.feeds.values()
        ]
        results = await asyncio.gather(*tasks)
        return all(results)

    def get_momentum(self, asset: str, seconds: int = 5) -> Optional[float]:
        """5-second momentum % for an asset (primary spike signal)."""
        feed = self.feeds.get(asset.upper())
        return feed.get_momentum_pct(seconds) if feed else None

    def get_price(self, asset: str) -> Optional[float]:
        """Current price for an asset."""
        feed = self.feeds.get(asset.upper())
        return feed.get_current_price() if feed else None

    def is_ready(self, asset: str) -> bool:
        """True if feed has received at least one tick."""
        feed = self.feeds.get(asset.upper())
        return feed.has_data() if feed else False

    def stop_all(self) -> None:
        """Stop all feeds and cancel tasks."""
        for feed in self.feeds.values():
            feed.stop()
        for task in self.tasks.values():
            task.cancel()
        self.logger.info("All Binance price feeds stopped")

    def get_summary(self) -> Dict:
        """Summary of all feed states."""
        return {
            asset: feed.get_summary()
            for asset, feed in self.feeds.items()
        }
