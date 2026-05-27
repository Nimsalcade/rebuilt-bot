#!/usr/bin/env python3
"""
Gabagool Bot - Spike Detector (Latency Arbitrage Signal)

Purpose:
    Monitors the real-time price feed for sudden directional spikes.
    This is the core signal engine for the latency-arbitrage strategy:
    detect a Binance/Coinbase price move BEFORE Polymarket books update,
    then snipe the stale orders.

Strategy:
    - Measure BTC price change over the last 5 seconds
    - If |change| > SPIKE_THRESHOLD_PCT → trigger a directional snipe
    - Implements a mandatory cooldown after each trigger to avoid
      double-firing on the same move

Signal hierarchy (applied in order):
    1. Cooldown active?   → no signal
    2. 5s spike > threshold? → fire directional spike
    3. Otherwise          → FARMING (no action)

Author: AI-Generated
Created: 2026-05-03
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ============================================================================
# Configuration
# ============================================================================

# Minimum % move in 5 seconds to trigger a snipe.
# 0.02% is the Gabagool22 specification. Tighten to catch smaller moves,
# loosen to reduce noise. (expressed as a plain %, e.g. 0.02 = 0.02%)
SPIKE_THRESHOLD_PCT = 0.02

# How many seconds to look back for spike measurement
SPIKE_WINDOW_S = 5

# After a snipe fires, how long to sit out before re-enabling (seconds).
# Prevents double-firing on the same trend continuation.
# g22 never pauses — 20s is enough to avoid double-firing without dead time.
COOLDOWN_AFTER_SNIPE_S = 20

# Minimum data age (seconds) before we trust the feed enough to fire
MIN_FEED_AGE_S = 3


# ============================================================================
# Data Classes
# ============================================================================

class SpikeSignal(Enum):
    NONE     = "NONE"     # Flat — farm both sides
    UP       = "UP"       # BTC spiked up → snipe UP shares
    DOWN     = "DOWN"     # BTC crashed down → snipe DOWN shares
    COOLDOWN = "COOLDOWN" # Post-snipe cooldown, no new signal


@dataclass
class SpikeResult:
    """Result of a spike detection check."""
    signal:       SpikeSignal
    momentum_5s:  Optional[float]   # Raw 5s % change
    current_price: Optional[float]  # Latest BTC price
    triggered_at:  Optional[float]  # Unix timestamp of trigger
    cooldown_remaining_s: float     # Seconds until cooldown expires

    @property
    def is_snipe(self) -> bool:
        return self.signal in (SpikeSignal.UP, SpikeSignal.DOWN)

    @property
    def direction(self) -> Optional[str]:
        if self.signal == SpikeSignal.UP:
            return "UP"
        if self.signal == SpikeSignal.DOWN:
            return "DOWN"
        return None

    def __str__(self) -> str:
        mom = f"{self.momentum_5s:+.4f}%" if self.momentum_5s is not None else "N/A"
        cd  = f" (cooldown {self.cooldown_remaining_s:.0f}s)" if self.cooldown_remaining_s > 0 else ""
        price_str = f"${self.current_price:.2f}" if self.current_price else "$N/A"
        return f"[{self.signal.value}] 5s_momentum={mom} price={price_str}{cd}"


# ============================================================================
# SpikeDetector
# ============================================================================

class SpikeDetector:
    """
    Latency-arbitrage spike detector.

    Usage:
        detector = SpikeDetector(threshold_pct=0.02)

        # Inside your main loop:
        result = detector.check(price_feed)

        if result.is_snipe:
            await execute_snipe(result.direction)
            detector.mark_fired()
    """

    def __init__(
        self,
        threshold_pct: float = SPIKE_THRESHOLD_PCT,
        window_s: int = SPIKE_WINDOW_S,
        cooldown_s: float = COOLDOWN_AFTER_SNIPE_S,
    ):
        self.threshold_pct = threshold_pct
        self.window_s      = window_s
        self.cooldown_s    = cooldown_s

        self._last_snipe_at: Optional[float] = None
        self.logger = logging.getLogger("spike_detector")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def check(self, price_feed) -> SpikeResult:
        """
        Evaluate the price feed for a spike. Call this as fast as possible
        (every tick or every ~0.1s from the main loop).

        Args:
            price_feed: A BinancePriceFeed instance

        Returns:
            SpikeResult describing the current state
        """
        now = time.time()
        current_price = price_feed.get_current_price()
        cooldown_remaining = self._cooldown_remaining(now)

        # --- Gate 1: Need live data ---
        if current_price is None or not price_feed.has_data():
            return SpikeResult(
                signal=SpikeSignal.NONE,
                momentum_5s=None,
                current_price=None,
                triggered_at=None,
                cooldown_remaining_s=cooldown_remaining,
            )

        # --- Gate 2: Cooldown active ---
        if cooldown_remaining > 0:
            momentum_5s = price_feed.get_momentum_pct(seconds=self.window_s)
            return SpikeResult(
                signal=SpikeSignal.COOLDOWN,
                momentum_5s=momentum_5s,
                current_price=current_price,
                triggered_at=self._last_snipe_at,
                cooldown_remaining_s=cooldown_remaining,
            )

        # --- Gate 3: Measure 5-second momentum ---
        momentum_5s = price_feed.get_momentum_pct(seconds=self.window_s)
        if momentum_5s is None:
            return SpikeResult(
                signal=SpikeSignal.NONE,
                momentum_5s=None,
                current_price=current_price,
                triggered_at=None,
                cooldown_remaining_s=0.0,
            )

        # --- Gate 4: Threshold check ---
        if momentum_5s >= self.threshold_pct:
            # Mark cooldown IMMEDIATELY (atomic) so concurrent windows
            # that check() in the same tick see COOLDOWN, not a second spike.
            was_in_cooldown = self.in_cooldown
            if not was_in_cooldown:
                self._last_snipe_at = time.time()
                asset = getattr(price_feed, 'asset', 'ASSET')
                self.logger.info(
                    "🚀 SPIKE UP  | 5s_move=%+.4f%% (threshold=%.4f%%) | %s=$%.2f",
                    momentum_5s, self.threshold_pct, asset, current_price
                )
                return SpikeResult(
                    signal=SpikeSignal.UP,
                    momentum_5s=momentum_5s,
                    current_price=current_price,
                    triggered_at=now,
                    cooldown_remaining_s=0.0,
                )
            # Already handled by another concurrent window
            return SpikeResult(
                signal=SpikeSignal.COOLDOWN,
                momentum_5s=momentum_5s,
                current_price=current_price,
                triggered_at=self._last_snipe_at,
                cooldown_remaining_s=self._cooldown_remaining(now),
            )

        if momentum_5s <= -self.threshold_pct:
            was_in_cooldown = self.in_cooldown
            if not was_in_cooldown:
                self._last_snipe_at = time.time()
                asset = getattr(price_feed, 'asset', 'ASSET')
                self.logger.info(
                    "💥 SPIKE DOWN | 5s_move=%+.4f%% (threshold=%.4f%%) | %s=$%.2f",
                    momentum_5s, self.threshold_pct, asset, current_price
                )
                return SpikeResult(
                    signal=SpikeSignal.DOWN,
                    momentum_5s=momentum_5s,
                    current_price=current_price,
                    triggered_at=now,
                    cooldown_remaining_s=0.0,
                )
            return SpikeResult(
                signal=SpikeSignal.COOLDOWN,
                momentum_5s=momentum_5s,
                current_price=current_price,
                triggered_at=self._last_snipe_at,
                cooldown_remaining_s=self._cooldown_remaining(now),
            )

        # No spike — flat/farming
        return SpikeResult(
            signal=SpikeSignal.NONE,
            momentum_5s=momentum_5s,
            current_price=current_price,
            triggered_at=None,
            cooldown_remaining_s=0.0,
        )

    def mark_fired(self) -> None:
        """
        Legacy: can still be called from MakerLoop after snipe execution.
        Now idempotent — check() already marks the cooldown atomically,
        so calling this again just refreshes the timestamp (harmless).
        """
        if not self.in_cooldown:
            self._last_snipe_at = time.time()
        self.logger.debug("mark_fired() called | cooldown=%.0fs remaining", self.cooldown_s)

    def reset_cooldown(self) -> None:
        """Force-clear cooldown (e.g. for testing)."""
        self._last_snipe_at = None

    @property
    def in_cooldown(self) -> bool:
        return self._cooldown_remaining(time.time()) > 0

    # -------------------------------------------------------------------------
    # Private
    # -------------------------------------------------------------------------

    def _cooldown_remaining(self, now: float) -> float:
        if self._last_snipe_at is None:
            return 0.0
        elapsed = now - self._last_snipe_at
        return max(0.0, self.cooldown_s - elapsed)
