#!/usr/bin/env python3
"""
Gabagool Bot - Pure Spread Maker Loop

Purpose:
    Per-window execution engine implementing the Gabagool22 strategy:
    Post passive GTC bids on both sides of every market. When both sides
    fill at a combined price below $1.00, merge the matched pairs immediately
    for risk-free profit. Let unmatched naked shares settle at expiry.

State Machine:
    FARMING → [window closing] → HOLD
    Any state → [window resolved] → DONE

Author: AI-Generated
Created: 2026-05-29
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple

from .merge_engine import MergeEngine, MIN_MERGE_SHARES
import src.terminal_ui as terminal_ui


# ============================================================================
# Configuration
# ============================================================================

# --- Farming parameters ---
FARM_ORDER_SHARES  = 10        
FARM_REFRESH_S     = 15.0      # How often to repost the full ladder
MAX_ORDER_AGE_S    = 15.0      # Cancel and repost if order older than this
FARM_MAX_SHARES    = 500       # Max total farming shares per side per window

# --- Gabagool22-style price ladder ---
LADDER_STEPS = [
    0.10, 0.15, 0.20,
    0.25, 0.30, 0.35,
    0.40, 0.45, 0.50,
    0.55, 0.60, 0.65,
    0.70, 0.75, 0.80,
]

# --- Dollar-targeted sizing per rung ---
RUNG_DOLLAR_TARGETS = [
    (0.00, 0.20, 1.00),   # floor lottery — $1 each
    (0.20, 0.40, 2.00),   # low zone      — $2 each
    (0.40, 0.90, 4.00),   # mid/high zone — $4 each
]

# --- Window close buffer ---
STOP_POSTING_BUFFER_S = 60     # Stop all orders 60s before window end

# --- Combined cost gate ---
MAX_COMBINED_COST_GATE = 0.97

# ============================================================================
# Data Classes
# ============================================================================

class LoopState(Enum):
    FARMING     = "FARMING"
    HOLD        = "HOLD"         # Near window close — hold all positions
    DONE        = "DONE"


@dataclass
class OrderRecord:
    """Track a single resting maker order."""
    order_id:  str
    side:      str     # "UP" or "DOWN"
    token_id:  str
    price:     float
    shares:    float
    placed_at: float   # unix timestamp


@dataclass
class WindowFillSummary:
    """Aggregated fill data for a completed window."""
    market_id:    str
    window_start: datetime
    window_end:   datetime

    up_fills:       int   = 0
    up_shares:      float = 0.0
    up_total_cost:    float = 0.0
    up_gross_shares:  float = 0.0

    down_fills:       int   = 0
    down_shares:      float = 0.0
    down_total_cost:  float = 0.0
    down_gross_shares: float = 0.0

    merged_usdc: float       = 0.0

    @property
    def up_avg_cost(self) -> float:
        return self.up_total_cost / self.up_gross_shares if self.up_gross_shares else 0.0

    @property
    def down_avg_cost(self) -> float:
        return self.down_total_cost / self.down_gross_shares if self.down_gross_shares else 0.0

    @property
    def total_invested(self) -> float:
        return self.up_total_cost + self.down_total_cost

    @property
    def merged_usdc_cost_basis(self) -> float:
        return self.merged_usdc * (self.up_avg_cost + self.down_avg_cost)
        
    @property
    def naked_cost_basis(self) -> float:
        return self.total_invested - self.merged_usdc_cost_basis

    @property
    def naked_shares(self) -> float:
        """Leftover unmatched shares after merging (all on the lean side).

        up_shares/down_shares are net of merged pairs, so the larger of the two
        is the naked leftover that settles at resolution.
        """
        return max(self.up_shares, self.down_shares)

    @property
    def lean_direction(self) -> Optional[str]:
        if self.up_shares > self.down_shares:
            return "UP"
        elif self.down_shares > self.up_shares:
            return "DOWN"
        return None

    def __str__(self) -> str:
        return f"WindowFillSummary(UP: {self.up_shares}@{self.up_avg_cost:.3f}, DOWN: {self.down_shares}@{self.down_avg_cost:.3f})"


# ============================================================================
# MakerLoop
# ============================================================================

class MakerLoop:
    """
    Pure spread arbitrage execution engine.
    Run one instance per active 15-minute market window.
    """

    def __init__(
        self,
        bot: Any,
        dry_run: bool = False,
        farm_shares: int = FARM_ORDER_SHARES,
        farm_max_shares: int = FARM_MAX_SHARES,
        stop_posting_buffer_s: int = STOP_POSTING_BUFFER_S,
        window_capital_cap: float = 250.0,
    ):
        self.bot                   = bot
        self.dry_run               = dry_run
        self.farm_shares           = farm_shares
        self.farm_max_shares       = farm_max_shares
        self.stop_posting_buffer_s = stop_posting_buffer_s
        self.window_capital_cap    = window_capital_cap
        self.logger                = logging.getLogger("maker_loop")
        self._cost_gate_logged_for_window: set = set()
        
        self._is_paused_up = False
        self._is_paused_down = False

        self.merge_engine = MergeEngine(bot=bot, dry_run=dry_run)

    def _dollar_target_shares(self, price: float) -> int:
        """Compute shares dynamically scaled by the available session capital."""
        scale_factor = max(1.0, self.window_capital_cap / 30.0)
        
        target = 1.00 * scale_factor
        for lo, hi, t in RUNG_DOLLAR_TARGETS:
            if lo <= price < hi:
                target = t * scale_factor
                break
                
        target = max(1.0, target)  # Polymarket $1 minimum
        shares = math.ceil(target / price)
        while shares * price < 1.00:
            shares += 1
        return shares

    async def run(
        self,
        market: Any,
        window_end: datetime,
        risk_manager: Any = None,
        stats_tracker: Any = None,
        db: Any = None,
    ) -> WindowFillSummary:
        """
        Run the full spread farming loop for one market window.
        """
        market_id   = market.id
        loop_start  = time.time()

        summary = WindowFillSummary(
            market_id=market_id,
            window_start=datetime.now(),
            window_end=window_end,
        )

        active_orders: Dict[str, OrderRecord] = {}
        state = LoopState.FARMING

        last_up_post_at:   float = 0.0
        last_down_post_at: float = 0.0

        cached_yes_bid: Optional[float] = None
        cached_no_bid:  Optional[float] = None
        last_bid_fetch_at: float = 0.0
        BID_CACHE_TTL_S = FARM_REFRESH_S

        last_status_log_at: float = 0.0
        STATUS_LOG_INTERVAL_S = 60.0

        self.logger.info(
            "MakerLoop starting for %s | end=%s | state=FARMING",
            market_id[:16], window_end.strftime("%H:%M:%S")
        )

        try:
            while state != LoopState.DONE:
                now        = time.time()
                elapsed_s  = now - loop_start
                seconds_to_end = (window_end - datetime.now()).total_seconds()

                if seconds_to_end <= 0:
                    self.logger.info("Window %s expired — ending loop", market_id[:16])
                    state = LoopState.DONE
                    break

                if seconds_to_end <= self.stop_posting_buffer_s and state != LoopState.HOLD:
                    self.logger.info(
                        "Window %s closing in %.0fs — entering HOLD",
                        market_id[:16], seconds_to_end
                    )
                    await self._cancel_all(active_orders)
                    
                    # Force merge any remaining pairs before expiration
                    if summary.up_shares > 0 and summary.down_shares > 0:
                        await self.merge_engine.try_merge(market, summary, force=True)
                        
                    state = LoopState.HOLD

                if state == LoopState.HOLD:
                    await asyncio.sleep(1.0)
                    continue

                if state == LoopState.FARMING:
                    # Fetch bids
                    need_bids = (now - last_bid_fetch_at) >= BID_CACHE_TTL_S
                    if need_bids:
                        try:
                            cached_yes_bid, cached_no_bid = await self._get_bids(market)
                            last_bid_fetch_at = now
                        except (ValueError, TypeError):
                            self.logger.warning("Market %s 404 — ending loop", market_id[:16])
                            break

                    if cached_yes_bid is None or cached_no_bid is None:
                        await asyncio.sleep(1.0)
                        continue

                    # Post farming orders
                    await self._farm_orders(
                        market, cached_yes_bid, cached_no_bid,
                        active_orders, summary,
                        last_up_post_at, last_down_post_at,
                        now,
                    )
                    
                    if (now - last_up_post_at) >= FARM_REFRESH_S:
                        last_up_post_at = now
                    if (now - last_down_post_at) >= FARM_REFRESH_S:
                        last_down_post_at = now

                    # Reconcile fills
                    await self._reconcile_fills(market_id, market.condition_id, active_orders, summary)

                    # Try to merge
                    if summary.up_shares > 0 and summary.down_shares > 0:
                        await self.merge_engine.try_merge(market, summary)

                    # Cancel stale
                    await self._cancel_stale(active_orders)

                    # Log status
                    if (now - last_status_log_at) >= STATUS_LOG_INTERVAL_S and elapsed_s > 1:
                        self._log_status(summary, elapsed_s, state)
                        last_status_log_at = now

                    await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            self.logger.info("MakerLoop cancelled: %s", market_id[:16])
        except Exception as e:
            self.logger.error("MakerLoop error: %s | %s", market_id[:16], e, exc_info=True)
        finally:
            await self._cancel_all(active_orders)

        naked_sh = summary.up_shares if summary.up_shares > summary.down_shares else summary.down_shares
        naked_side = "UP" if summary.up_shares > summary.down_shares else "DOWN"
        
        cost = summary.total_invested
        merged = summary.merged_usdc
        rough_net = merged - cost
        
        msg = terminal_ui.fmt_window_close(
            market_id=market_id,
            cost=cost,
            merged=merged,
            naked_shares=naked_sh,
            naked_side=naked_side,
            net_pnl=rough_net,
            lean=summary.lean_direction or "even",
            signal=None
        )
        print(msg, flush=True)
        self.logger.info("MakerLoop complete: %s", market_id[:16])

        self.merge_engine.log_session_summary()

        if stats_tracker:
            try:
                stats_tracker.record_trade(
                    market_id=market_id,
                    yes_price=summary.up_avg_cost,
                    no_price=summary.down_avg_cost,
                    profit_margin=0.0,
                    trade_size=summary.total_invested / 2,
                    notes=f"lean={summary.lean_direction or 'none'}",
                )
            except Exception:
                pass

        return summary

    # =========================================================================
    # Farming
    # =========================================================================

    async def _farm_orders(
        self,
        market: Any,
        yes_bid: float,
        no_bid: float,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
        last_up_post_at: float,
        last_down_post_at: float,
        now: float,
    ) -> None:
        """Post the full price ladder on both sides."""
        
        # Combined cost gate (global check)
        if summary.up_shares > 0 and summary.down_shares > 0:
            combined = summary.up_avg_cost + summary.down_avg_cost
            if combined > MAX_COMBINED_COST_GATE:
                if summary.market_id not in self._cost_gate_logged_for_window:
                    print(terminal_ui.fmt_gate(
                        summary.market_id,
                        summary.up_avg_cost,
                        summary.down_avg_cost,
                        combined,
                        MAX_COMBINED_COST_GATE
                    ), flush=True)
                    self._cost_gate_logged_for_window.add(summary.market_id)
                return  # stop posting

        # Hard capital cap (Capital at Work)
        held_value = (summary.up_shares * summary.up_avg_cost + summary.down_shares * summary.down_avg_cost)
        locked_resting = sum(o.price * o.shares for o in list(active_orders.values()))
        capital_at_work = held_value + locked_resting
        if capital_at_work >= self.window_capital_cap:
            return

        # NEW: Inventory Balance Cap (Max Lean) with Hysteresis
        # Prevent accumulating a massive directional position by halting the heavy side
        # and actively cancelling its resting orders to prevent soft-cap leakage.
        
        up, dn = summary.up_shares, summary.down_shares
        lean_up = (up - dn) / max(up, dn, 1)
        lean_dn = (dn - up) / max(up, dn, 1)
        
        # Enter pause on the upper threshold
        if not self._is_paused_up and lean_up > 0.15 and (up - dn) > 10.0:
            self._is_paused_up = True
            # Actively cancel resting UP orders to stop the bleed
            for oid, o in list(active_orders.items()):
                if o.side == "UP":
                    await self._cancel_order(oid, active_orders)
                    
        if not self._is_paused_down and lean_dn > 0.15 and (dn - up) > 10.0:
            self._is_paused_down = True
            # Actively cancel resting DOWN orders to stop the bleed
            for oid, o in list(active_orders.items()):
                if o.side == "DOWN":
                    await self._cancel_order(oid, active_orders)

        # Exit pause only on the lower threshold
        if self._is_paused_up and lean_up < 0.06:
            self._is_paused_up = False
            
        if self._is_paused_down and lean_dn < 0.06:
            self._is_paused_down = False

        post_up = not self._is_paused_up
        post_down = not self._is_paused_down

        if post_up and summary.up_shares < self.farm_max_shares:
            if (now - last_up_post_at) >= FARM_REFRESH_S:
                await self._post_ladder(
                    market.yes_token_id, "UP", active_orders, summary
                )

        if post_down and summary.down_shares < self.farm_max_shares:
            if (now - last_down_post_at) >= FARM_REFRESH_S:
                await self._post_ladder(
                    market.no_token_id, "DOWN", active_orders, summary
                )


    async def _post_ladder(
        self,
        token_id: str,
        side: str,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> None:
        """Post the full price ladder sequentially to avoid 425 rate-limit errors."""
        placed = 0
        for price in LADDER_STEPS:
            result = await self._post_ladder_rung(
                token_id, side, price, active_orders, summary
            )
            if result:
                placed += 1
            await asyncio.sleep(0.08)   # 80ms between rungs = ~1.2s for full ladder
        
        self.logger.debug(
            "Ladder posted: %s %s | %d/%d rungs",
            side, summary.market_id[:12], placed, len(LADDER_STEPS)
        )

    async def _post_ladder_rung(
        self,
        token_id: str,
        side: str,
        price: float,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> bool:
        """Post a single GTC limit order at one rung of the ladder with per-rung pre-checks."""
        
        # Per-rung pre-check: would this rung push the combined average over the gate?
        if side == "UP" and summary.down_gross_shares > 0:
            projected = price + summary.down_avg_cost
            if projected > MAX_COMBINED_COST_GATE:
                return False  # skip this rung — too expensive
        elif side == "DOWN" and summary.up_gross_shares > 0:
            projected = summary.up_avg_cost + price
            if projected > MAX_COMBINED_COST_GATE:
                return False  # skip this rung — too expensive

        # Skip if already covered
        for o in list(active_orders.values()):
            if o.side == side and abs(o.price - price) < 0.03:
                return False

        shares = self._dollar_target_shares(price)

        if self.dry_run:
            fake_id = f"dry_{side}_{int(price*100)}_{int(time.time()*1000)%10000}"
            active_orders[fake_id] = OrderRecord(
                order_id=fake_id, side=side, token_id=token_id,
                price=price, shares=shares, placed_at=time.time(),
            )
            return True

        try:
            result = await asyncio.to_thread(
                self.bot.place_order, token_id, price, float(shares), "BUY", "GTC"
            )
            if not result:
                return False
            order_id = result.get("orderID", f"live_{int(time.time())}")
            active_orders[order_id] = OrderRecord(
                order_id=order_id, side=side, token_id=token_id,
                price=price, shares=shares, placed_at=time.time(),
            )
            return True
        except Exception as e:
            self.logger.debug("Ladder rung error %s $%.2f: %s", side, price, e)
            return False


    # =========================================================================
    # Order management
    # =========================================================================

    async def _cancel_stale(self, active_orders: Dict[str, OrderRecord]) -> None:
        now = time.time()
        stale = [oid for oid, o in list(active_orders.items()) if now - o.placed_at > MAX_ORDER_AGE_S]
        for oid in stale:
            await self._cancel_order(oid, active_orders)

    async def _cancel_all(self, active_orders: Dict[str, OrderRecord]) -> None:
        for oid in list(active_orders.keys()):
            await self._cancel_order(oid, active_orders)

    async def _cancel_order(self, order_id: str, active_orders: Dict[str, OrderRecord]) -> None:
        if order_id not in active_orders:
            return
        if self.dry_run:
            active_orders.pop(order_id, None)
            return
        try:
            await asyncio.to_thread(self.bot.cancel_order, order_id)
        except Exception:
            pass
        finally:
            active_orders.pop(order_id, None)

    # =========================================================================
    # Market Data & Recon
    # =========================================================================

    async def _get_bids(self, market: Any) -> Tuple[Optional[float], Optional[float]]:
        if self.dry_run:
            return 0.30, 0.70
        try:
            yes_spread, no_spread = await asyncio.gather(
                asyncio.to_thread(self.bot.get_spread, market.yes_token_id),
                asyncio.to_thread(self.bot.get_spread, market.no_token_id),
            )
            yes_bid = yes_spread.get("bid", 0.0)
            no_bid  = no_spread.get("bid", 0.0)
            if yes_bid <= 0 or no_bid <= 0:
                return None, None
            return yes_bid, no_bid
        except ValueError as e:
            if "Not Found" in str(e):
                raise
            return None, None
        except Exception:
            return None, None

    async def _reconcile_fills(
        self,
        market_id: str,
        condition_id: str,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary
    ) -> None:
        """Poll get_open_orders() and mark missing orders as filled."""
        if not active_orders:
            return

        live_ids = []
        if not self.dry_run:
            try:
                live_orders = await asyncio.to_thread(self.bot.get_open_orders, condition_id)
                live_ids = [o.get("id") or o.get("orderID") for o in live_orders]
            except Exception as e:
                self.logger.debug("Reconcile error: %s", e)
                return
        else:
            live_ids = [oid for oid in active_orders.keys() if "dry_" in oid]

        filled_ids = []
        for oid, order in active_orders.items():
            if oid not in live_ids:
                if self.dry_run:
                    import random
                    if random.random() > 0.8:  # 20% fake fill rate
                        filled_ids.append(oid)
                else:
                    filled_ids.append(oid)

        for oid in filled_ids:
            order = active_orders.pop(oid)
            if order.side == "UP":
                summary.up_fills += 1
                summary.up_shares += order.shares
                summary.up_total_cost += (order.price * order.shares)
                summary.up_gross_shares += order.shares
            else:
                summary.down_fills += 1
                summary.down_shares += order.shares
                summary.down_total_cost += (order.price * order.shares)
                summary.down_gross_shares += order.shares

            print(terminal_ui.fmt_fill(
                market_id=market_id,
                side=order.side,
                price=order.price,
                shares=order.shares,
                cost=order.price * order.shares,
            ), flush=True)

    def _log_status(self, summary: WindowFillSummary, elapsed: float, state: LoopState):
        up_avg = summary.up_avg_cost
        dn_avg = summary.down_avg_cost
        c_str = f"${up_avg + dn_avg:.2f}" if up_avg and dn_avg else "n/a"
        
        self.logger.info(
            "[%02d:%02d] %s | %s | UP: %d sh @ $%.2f | DN: %d sh @ $%.2f | Comb: %s",
            int(elapsed // 60), int(elapsed % 60),
            summary.market_id[:16],
            state.value,
            summary.up_shares, up_avg,
            summary.down_shares, dn_avg,
            c_str
        )
