#!/usr/bin/env python3
"""
Gabagool Bot - Sniper Execution Engine (Latency Arbitrage)

Purpose:
    Executes the cancel-and-snipe pattern when a spike signal fires:
    1. Instantly cancel ALL resting orders on the OPPOSING side
    2. Fire an aggressive "marketable limit" order on the WINNING side
       priced slightly above the current ask to guarantee a fill

This is the "Phase 3 - The Snipe" from the Gabagool22 playbook.

Design:
    - Uses asyncio.gather() to fire cancels + new order in parallel
    - Limit price: best_ask + SNIPE_ASK_BUFFER (default $0.02)
    - Hard ceiling: MAX_SNIPE_PRICE (default $0.85) — never overpay
    - Snipe order size: SNIPE_SHARES (default 50, much larger than farming)

Author: AI-Generated
Created: 2026-05-03
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from src.maker_loop import OrderRecord, WindowFillSummary


# ============================================================================
# Configuration
# ============================================================================

# Snipe order size (shares).
# Scaled for $58 capital: 10 shares × $0.85 max = $8.50 per market.
# 2 markets × $8.50 = $17 max snipe exposure — safe within $58 balance.
SNIPE_SHARES = 10

# How much above the current ASK to price the snipe limit order.
# $0.02 above ask ensures we're first in queue at that price level.
SNIPE_ASK_BUFFER = 0.02

# Never pay more than this per share when sniping.
MAX_SNIPE_PRICE = 0.85

# Maximum combined cost of the snipe pair (Snipe Price + Opposing Avg Cost)
MAX_COMBINED_COST = 0.98

# Minimum share ask price to consider worth sniping
MIN_SNIPE_PRICE = 0.10

# Timeout for the snipe execution in seconds
SNIPE_TIMEOUT_S = 2.0

# Order type for snipe — GTC like gabagool22 (not FOK)
# His orders sit on the book as limit orders, not fill-or-kill
SNIPE_ORDER_TYPE = "GTC"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class SnipeResult:
    """Outcome of a snipe execution attempt."""
    success:        bool
    direction:      str               # "UP" or "DOWN"
    price_paid:     Optional[float]   # Limit price submitted
    shares:         int               # Shares requested
    order_id:       Optional[str]     # Exchange order ID if placed
    cancels_fired:  int               # Number of opposing orders cancelled
    latency_ms:     float             # Total execution time in ms
    error:          Optional[str]     # Error message if failed

    def __str__(self) -> str:
        status = "✅ OK" if self.success else f"❌ FAIL ({self.error})"
        return (
            f"Snipe[{self.direction}] {status} | "
            f"price=${self.price_paid:.3f} × {self.shares}sh | "
            f"cancels={self.cancels_fired} | "
            f"latency={self.latency_ms:.1f}ms"
        )


# ============================================================================
# Sniper
# ============================================================================

class Sniper:
    """
    Cancel-and-snipe execution engine.

    Usage:
        sniper = Sniper(bot, dry_run=False, snipe_shares=50)
        result = await sniper.execute(
            direction="UP",
            market=market,
            active_orders=active_orders,
            summary=summary,
        )
    """

    def __init__(
        self,
        bot: Any,
        dry_run: bool = False,
        snipe_shares: int = SNIPE_SHARES,
        ask_buffer: float = SNIPE_ASK_BUFFER,
        max_price: float = MAX_SNIPE_PRICE,
    ):
        self.bot          = bot
        self.dry_run      = dry_run
        self.snipe_shares = snipe_shares
        self.ask_buffer   = ask_buffer
        self.max_price    = max_price
        self.logger       = logging.getLogger("sniper")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def execute(
        self,
        direction: str,
        market: Any,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> SnipeResult:
        """
        Execute the full cancel-and-snipe sequence.

        Steps (all async, as parallel as possible):
          1. Cancel all opposing-side resting orders
          2. Get current ask on the winning side
          3. Post aggressive limit order at ask + buffer

        Args:
            direction:     "UP" or "DOWN"
            market:        Market object with yes_token_id / no_token_id
            active_orders: Current resting order tracking dict (mutated in place)
            summary:       Window fill summary (mutated in place on fill)

        Returns:
            SnipeResult
        """
        start_ns = time.monotonic_ns()
        opposing = "DOWN" if direction == "UP" else "UP"

        # Select the correct token IDs
        snipe_token_id   = market.yes_token_id if direction == "UP" else market.no_token_id
        opposing_token_id = market.no_token_id if direction == "UP" else market.yes_token_id

        self.logger.info(
            "⚡ SNIPE INITIATED | direction=%s | market=%s | resting_orders=%d",
            direction, summary.market_id[:16], len(active_orders)
        )

        try:
            # ── Phase 1: Get ask price (we need this to compute snipe price) ──
            # This is a single GET to the CLOB — typically 40-60ms over
            # persistent httpx connection. We cannot skip this because
            # the snipe limit price = ask + buffer.
            ask_price = await self._get_ask(snipe_token_id)

            if ask_price is None:
                latency_ms = (time.monotonic_ns() - start_ns) / 1e6
                self.logger.warning("Snipe aborted — could not get ask price for %s", direction)
                return SnipeResult(
                    success=False, direction=direction, price_paid=None,
                    shares=self.snipe_shares, order_id=None, cancels_fired=0,
                    latency_ms=latency_ms, error="no_ask_price",
                )

            snipe_price = min(round(ask_price + self.ask_buffer, 2), self.max_price)

            if snipe_price < MIN_SNIPE_PRICE:
                latency_ms = (time.monotonic_ns() - start_ns) / 1e6
                return SnipeResult(
                    success=False, direction=direction, price_paid=snipe_price,
                    shares=self.snipe_shares, order_id=None, cancels_fired=0,
                    latency_ms=latency_ms, error=f"price_too_low_{snipe_price:.2f}",
                )

            # ── Check if we have opposing inventory for a safe arbitrage ──
            opposing_shares = summary.down_shares if direction == "UP" else summary.up_shares
            opposing_avg_cost = summary.down_avg_cost if direction == "UP" else summary.up_avg_cost

            if opposing_shares < self.snipe_shares:
                latency_ms = (time.monotonic_ns() - start_ns) / 1e6
                self.logger.warning("Snipe aborted — insufficient opposing shares (%d < %d)", opposing_shares, self.snipe_shares)
                return SnipeResult(
                    success=False, direction=direction, price_paid=snipe_price,
                    shares=self.snipe_shares, order_id=None, cancels_fired=0,
                    latency_ms=latency_ms, error="insufficient_opposing_shares",
                )

            combined_cost = snipe_price + opposing_avg_cost
            if combined_cost > MAX_COMBINED_COST:
                latency_ms = (time.monotonic_ns() - start_ns) / 1e6
                self.logger.warning("Snipe aborted — combined cost too high ($%.3f + $%.3f = $%.3f > $%.3f)", snipe_price, opposing_avg_cost, combined_cost, MAX_COMBINED_COST)
                return SnipeResult(
                    success=False, direction=direction, price_paid=snipe_price,
                    shares=self.snipe_shares, order_id=None, cancels_fired=0,
                    latency_ms=latency_ms, error="combined_cost_too_high",
                )

            # ── Phase 2: FIRE snipe + cancel opposing ─────────────────────
            # If free capital is insufficient to cover the snipe cost, we must
            # cancel opposing resting orders first to free collateral before
            # posting the new FOK order. Otherwise we fire both concurrently.
            free_balance = await asyncio.to_thread(self.bot._available_balance_micro)
            estimated_cost_micro = int(snipe_price * self.snipe_shares * 1_000_000)

            if free_balance is not None and free_balance < estimated_cost_micro:
                self.logger.debug(
                    "Low balance ($%.4f < $%.4f) — sequential cancel-then-snipe",
                    free_balance / 1_000_000, estimated_cost_micro / 1_000_000,
                )
                cancels_fired = await self._cancel_side(opposing, active_orders)
                order_id = await self._fire_order(snipe_token_id, snipe_price)
            else:
                snipe_task  = asyncio.create_task(self._fire_order(snipe_token_id, snipe_price))
                cancel_task = asyncio.create_task(self._cancel_side(opposing, active_orders))
                order_id, cancels_fired = await asyncio.gather(snipe_task, cancel_task)

            latency_ms = (time.monotonic_ns() - start_ns) / 1e6

            if order_id:
                self._record_snipe_fill(direction, snipe_price, active_orders, summary)
                self.logger.info(
                    "⚡ SNIPE COMPLETE | %s | %s",
                    direction, SnipeResult(
                        success=True, direction=direction,
                        price_paid=snipe_price, shares=self.snipe_shares,
                        order_id=order_id, cancels_fired=cancels_fired,
                        latency_ms=latency_ms, error=None
                    )
                )
                return SnipeResult(
                    success=True, direction=direction,
                    price_paid=snipe_price, shares=self.snipe_shares,
                    order_id=order_id, cancels_fired=cancels_fired,
                    latency_ms=latency_ms, error=None,
                )
            else:
                return SnipeResult(
                    success=False, direction=direction,
                    price_paid=snipe_price, shares=self.snipe_shares,
                    order_id=None, cancels_fired=cancels_fired,
                    latency_ms=latency_ms, error="order_placement_failed",
                )

        except Exception as e:
            latency_ms = (time.monotonic_ns() - start_ns) / 1e6
            self.logger.error("Snipe execution error: %s", e, exc_info=True)
            return SnipeResult(
                success=False, direction=direction, price_paid=None,
                shares=self.snipe_shares, order_id=None, cancels_fired=0,
                latency_ms=latency_ms, error=str(e),
            )

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    async def _cancel_side(
        self,
        side: str,
        active_orders: Dict[str, OrderRecord],
    ) -> int:
        """Cancel all resting orders on the specified side. Returns count cancelled."""
        to_cancel = [
            (oid, order) for oid, order in active_orders.items()
            if order.side == side
        ]

        if not to_cancel:
            return 0

        self.logger.debug("Cancelling %d %s orders...", len(to_cancel), side)

        async def _cancel_one(order_id: str) -> bool:
            if self.dry_run:
                self.logger.debug("DRY RUN: CANCEL %s", order_id[:16])
                active_orders.pop(order_id, None)
                return True
            try:
                success = await asyncio.to_thread(self.bot.cancel_order, order_id)
                active_orders.pop(order_id, None)
                return bool(success)
            except Exception as e:
                self.logger.debug("Cancel error %s: %s", order_id[:12], e)
                active_orders.pop(order_id, None)
                return False

        results = await asyncio.gather(*[_cancel_one(oid) for oid, _ in to_cancel])
        cancelled = sum(1 for r in results if r)
        self.logger.debug("Cancelled %d/%d %s orders", cancelled, len(to_cancel), side)
        return cancelled

    async def _get_ask(self, token_id: str) -> Optional[float]:
        """
        Get the current best ask (lowest ask) for a token.
        Always hits the real CLOB — even in dry run, since it's a read-only GET
        and gives us accurate pricing for paper PnL.
        """
        try:
            spread = await asyncio.to_thread(self.bot.get_spread, token_id)
            ask = spread.get("ask") or spread.get("best_ask")
            if ask:
                ask_f = float(ask)
                self.logger.debug("Ask for %s: $%.3f", token_id[:12], ask_f)
                return ask_f
            # Fallback: if book has no ask, snipe aborts (correct behavior)
            self.logger.debug("No ask available for %s", token_id[:12])
            return None
        except Exception as e:
            self.logger.debug("Error getting ask: %s", e)
            return None

    async def _fire_order(self, token_id: str, price: float) -> Optional[str]:
        """Place the snipe limit order. Returns order_id or None on failure."""
        if self.dry_run:
            import random
            fake_id = f"snipe_{int(time.time() * 1000) % 100000}"

            # Simulate realistic FOK fill rate.
            # In real markets, an FOK at ask+buffer gets rejected ~50% of the
            # time (book swept, price moved, insufficient depth at that level).
            # This makes paper PnL closer to live expectations.
            if price > self.max_price:
                self.logger.info(
                    "DRY RUN SNIPE: ❌ REJECTED (price $%.3f > max $%.3f) — FOK aborted",
                    price, self.max_price
                )
                return None  # FOK rejected: price too high

            if random.random() < 0.45:  # ~45% of FOK snipes fail in thin markets
                self.logger.info(
                    "DRY RUN SNIPE: ❌ FOK FAILED (no liquidity at $%.3f) — simulated miss",
                    price
                )
                return None

            self.logger.info(
                "DRY RUN SNIPE: ✅ FILLED %d shares @ $%.3f | id=%s",
                self.snipe_shares, price, fake_id
            )
            return fake_id

        try:
            result = await asyncio.to_thread(
                self.bot.place_order,
                token_id,
                price,
                float(self.snipe_shares),
                "BUY",
                "GTC",  # GTC limit — sits on book like gabagool22, fills naturally
            )
            if result:
                order_id = result.get("orderID", f"snipe_{int(time.time())}")
                self.logger.debug("Snipe order placed: id=%s", order_id[:16])
                return order_id
            return None
        except Exception as e:
            self.logger.error("Snipe order placement failed: %s", e)
            return None

    def _record_snipe_fill(
        self,
        direction: str,
        price: float,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> None:
        """Record the snipe as a fill in the window summary."""
        cost = self.snipe_shares * price

        if direction == "UP":
            summary.up_fills += 1
            summary.up_shares += self.snipe_shares
            summary.up_gross_shares += self.snipe_shares
            summary.up_total_cost += cost
        else:
            summary.down_fills += 1
            summary.down_shares += self.snipe_shares
            summary.down_gross_shares += self.snipe_shares
            summary.down_total_cost += cost

        self.logger.info(
            "Snipe fill recorded: %s %s @ $%.3f × %d | cost=$%.2f",
            direction, summary.market_id[:12], price, self.snipe_shares, cost
        )
