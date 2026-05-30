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

# --- Book-aware tight ladder ---
# gabagool's edge is refusal, not pricing: he only posts where the live book
# already sums below 1.00, and he posts AT the touch — not across a static
# 0.10–0.80 ladder that gets adversely selected at its expensive top rungs.
# So instead of a fixed ladder we read the live best ask on each side and post
# a few tight rungs just below it, but only when the combined touch leaves room.
TICK_SIZE          = 0.01     # Polymarket price increment
TIGHT_LADDER_RUNGS = 3        # rungs to post just below each side's best ask
TIGHT_LADDER_STEP  = 0.01     # spacing between tight rungs (in price)

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
        cached_yes_ask: Optional[float] = None
        cached_no_ask:  Optional[float] = None
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
                            (cached_yes_bid, cached_no_bid,
                             cached_yes_ask, cached_no_ask) = await self._get_quotes(market)
                            last_bid_fetch_at = now
                        except (ValueError, TypeError):
                            self.logger.warning("Market %s 404 — ending loop", market_id[:16])
                            break

                    if (cached_yes_bid is None or cached_no_bid is None
                            or cached_yes_ask is None or cached_no_ask is None):
                        await asyncio.sleep(1.0)
                        continue

                    # Post farming orders
                    await self._farm_orders(
                        market, cached_yes_bid, cached_no_bid,
                        cached_yes_ask, cached_no_ask,
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
        yes_ask: float,
        no_ask: float,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
        last_up_post_at: float,
        last_down_post_at: float,
        now: float,
    ) -> None:
        """Post tight rungs against the live book — but only when it's cheap.

        The default action is to NOT trade. We only post when the combined cost
        AT THE TOUCH (the two best asks we'd actually fill against) leaves a
        spread to capture; otherwise forcing fills just buys losing pairs.
        """

        # --- Primary refusal: combined cost at the live touch ---
        # best_ask_up + best_ask_down is what a pair costs RIGHT NOW. If it's
        # already at/above the gate there is no spread to capture, so we post
        # nothing this cycle. This is the single line that stops us from ever
        # buying a pair above the gate, evaluated against the live asks every
        # cycle — not against historical averages, and not gated on whether we
        # already hold fills.
        combined_touch = yes_ask + no_ask
        if combined_touch >= MAX_COMBINED_COST_GATE:
            if summary.market_id not in self._cost_gate_logged_for_window:
                print(terminal_ui.fmt_gate(
                    summary.market_id,
                    yes_ask,
                    no_ask,
                    combined_touch,
                    MAX_COMBINED_COST_GATE
                ), flush=True)
                self._cost_gate_logged_for_window.add(summary.market_id)
            return  # market isn't cheap — refuse it this cycle
        # Market became cheap again — allow the gate banner to re-log if it
        # later closes back up.
        self._cost_gate_logged_for_window.discard(summary.market_id)

        # Secondary safety: realized combined average gate (defends against a
        # book that moved against us between post and fill).
        if summary.up_shares > 0 and summary.down_shares > 0:
            combined = summary.up_avg_cost + summary.down_avg_cost
            if combined > MAX_COMBINED_COST_GATE:
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
                # UP rungs sit just below the UP best ask; gated against the
                # live DOWN best ask (the price the other leg would fill at).
                await self._post_ladder(
                    market.yes_token_id, "UP", yes_ask, no_ask,
                    active_orders, summary
                )

        if post_down and summary.down_shares < self.farm_max_shares:
            if (now - last_down_post_at) >= FARM_REFRESH_S:
                await self._post_ladder(
                    market.no_token_id, "DOWN", no_ask, yes_ask,
                    active_orders, summary
                )


    def _build_tight_ladder(self, best_ask: float, other_ask: float) -> list:
        """Rungs just below our best ask that still clear the combined gate.

        We bid into the touch (one tick below best_ask, then a few ticks down)
        rather than spanning the whole 0.10–0.80 range. Every rung is checked
        against the LIVE opposite ask so the pair can never sum to the gate —
        this is the absolute, fill-by-fill version of the cost gate.
        """
        rungs: list = []
        top = round(best_ask - TICK_SIZE, 2)
        for i in range(TIGHT_LADDER_RUNGS):
            price = round(top - i * TIGHT_LADDER_STEP, 2)
            if price <= 0.0:
                break
            # Absolute per-rung gate vs the live opposite ask.
            if round(price + other_ask, 4) > MAX_COMBINED_COST_GATE:
                continue
            rungs.append(price)
        return rungs

    async def _post_ladder(
        self,
        token_id: str,
        side: str,
        best_ask: float,
        other_ask: float,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> None:
        """Post a few tight rungs just below our best ask, gated on the live touch."""
        rungs = self._build_tight_ladder(best_ask, other_ask)
        placed = 0
        for price in rungs:
            result = await self._post_ladder_rung(
                token_id, side, price, other_ask, active_orders, summary
            )
            if result:
                placed += 1
            await asyncio.sleep(0.08)   # 80ms between rungs to avoid 425 rate-limits

        self.logger.debug(
            "Tight ladder posted: %s %s | %d/%d rungs (ask=%.2f, other_ask=%.2f)",
            side, summary.market_id[:12], placed, len(rungs), best_ask, other_ask
        )

    async def _post_ladder_rung(
        self,
        token_id: str,
        side: str,
        price: float,
        other_ask: float,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> bool:
        """Post a single GTC limit order at one rung with an absolute per-rung gate."""

        # Absolute per-rung pre-check: this rung's fill price plus the price the
        # OTHER leg would fill at (its live best ask) must clear the gate. This
        # fires on every post, with or without existing fills — so we never
        # complete a pair above the gate.
        if round(price + other_ask, 4) > MAX_COMBINED_COST_GATE:
            return False  # skip this rung — completing the pair would be too expensive

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

    async def _get_quotes(
        self, market: Any
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Return (yes_bid, no_bid, yes_ask, no_ask) from the live book.

        The asks are the prices we would actually FILL at as a buyer, so the
        entry logic gates on them — not on the bids. Returns Nones if the book
        is missing a side so callers can skip this cycle instead of guessing.
        """
        if self.dry_run:
            # Coherent fake book that sums below the gate (0.48 + 0.48 = 0.96)
            # so dry runs still exercise the posting path.
            return 0.46, 0.46, 0.48, 0.48
        try:
            yes_spread, no_spread = await asyncio.gather(
                asyncio.to_thread(self.bot.get_spread, market.yes_token_id),
                asyncio.to_thread(self.bot.get_spread, market.no_token_id),
            )
            yes_bid = yes_spread.get("bid", 0.0)
            no_bid  = no_spread.get("bid", 0.0)
            yes_ask = yes_spread.get("ask", 0.0)
            no_ask  = no_spread.get("ask", 0.0)
            if yes_bid <= 0 or no_bid <= 0 or yes_ask <= 0 or no_ask <= 0:
                return None, None, None, None
            return yes_bid, no_bid, yes_ask, no_ask
        except ValueError as e:
            if "Not Found" in str(e):
                raise
            return None, None, None, None
        except Exception:
            return None, None, None, None

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
