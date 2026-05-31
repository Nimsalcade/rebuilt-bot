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
FARM_MAX_SHARES    = 500       # Max total farming shares per side per window

# --- Maker quoting parameters ---
# We are a MAKER. We post resting buy orders INSIDE the spread, summing to at
# most TARGET_COMBINED, split by each side's fair value, and re-price them as
# the book moves. We NEVER cross the ask (that would make us a taker, which a
# healthy book — summing to ~1.01 at the asks — turns into a guaranteed loss).
TARGET_COMBINED    = 0.97     # max sum of our two resting bids = the merge profit budget
TICK               = 0.01     # Polymarket min price increment
REQUOTE_INTERVAL_S = 3.0      # how often we re-price (TUNE LIVE — start at 3s)
STALE_DRIFT        = 0.02     # re-quote a side if its resting price drifts this far from target
MIN_ORDER_SHARES   = 5        # Polymarket rejects orders below 5 shares ("Size (n) lower than the minimum: 5")

# --- Dollar-targeted sizing per rung ---
RUNG_DOLLAR_TARGETS = [
    (0.00, 0.20, 1.00),   # floor lottery — $1 each
    (0.20, 0.40, 2.00),   # low zone      — $2 each
    (0.40, 0.90, 4.00),   # mid/high zone — $4 each
]

# --- Window close buffer ---
STOP_POSTING_BUFFER_S = 60     # Stop all orders 60s before window end

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
        # Polymarket also enforces a 5-share minimum regardless of notional, so
        # floor here — otherwise high-priced rungs (small share counts) get
        # rejected with "Size (n) lower than the minimum: 5".
        return max(shares, MIN_ORDER_SHARES)

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
        BID_CACHE_TTL_S = REQUOTE_INTERVAL_S

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

                    # Maker quote cycle: rest bids inside the spread
                    await self._quote_market(
                        market, cached_yes_bid, cached_no_bid,
                        cached_yes_ask, cached_no_ask,
                        active_orders, summary,
                        last_up_post_at, last_down_post_at,
                        now,
                    )
                    
                    if (now - last_up_post_at) >= REQUOTE_INTERVAL_S:
                        last_up_post_at = now
                    if (now - last_down_post_at) >= REQUOTE_INTERVAL_S:
                        last_down_post_at = now

                    # Reconcile fills
                    await self._reconcile_fills(market_id, market.condition_id, active_orders, summary)

                    # Merge matched pairs IMMEDIATELY — locked $1.00 pairs can no
                    # longer be adversely selected; only the naked remainder can.
                    if summary.up_shares > 0 and summary.down_shares > 0:
                        await self.merge_engine.try_merge(market, summary)

                    # NOTE: no age-based stale-cancel here. As a maker we keep a
                    # resting bid in place as long as it's well-priced (preserving
                    # queue position); _quote_market re-quotes only on price drift.

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

    def _compute_maker_bids(
        self, yes_bid: float, no_bid: float, yes_ask: float, no_ask: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """Fair-value split of the TARGET_COMBINED budget, capped below each ask.

        Implements PRD steps 2-5: price each side off the mid-implied fair value,
        allocate the $0.97 budget proportionally, then cap each bid one tick under
        its ask so we are ALWAYS a maker — never crossing the spread.
        """
        mid_up   = (yes_bid + yes_ask) / 2.0
        mid_down = (no_bid + no_ask) / 2.0
        total    = mid_up + mid_down
        if total <= 0:
            return None, None
        fair_up   = mid_up   / total
        fair_down = mid_down / total

        # Step 3: split the budget by fair value (sums to TARGET_COMBINED).
        target_up   = TARGET_COMBINED * fair_up
        target_down = TARGET_COMBINED * fair_down

        # Step 4: cap strictly below the ask so we never take.
        up_bid   = round(min(target_up,   yes_ask - TICK), 2)
        down_bid = round(min(target_down, no_ask  - TICK), 2)

        # Step 5: rounding can push the sum over budget — shave the larger side.
        while round(up_bid + down_bid, 2) > TARGET_COMBINED:
            if up_bid >= down_bid:
                up_bid = round(up_bid - TICK, 2)
            else:
                down_bid = round(down_bid - TICK, 2)
        return up_bid, down_bid

    async def _quote_market(
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
        """Maker quote cycle: rest bids INSIDE the spread, summing <= TARGET_COMBINED.

        We do NOT read the ask and refuse. We compute fair-value bids under both
        asks, post them as resting GTC orders, and re-price on drift. We get
        filled by sellers crossing to us — earning the spread instead of paying
        it. Inventory is balanced by the existing deadband; matched pairs are
        merged immediately by the caller.
        """

        # Hard capital cap (Capital at Work) — unchanged guard.
        held_value = (summary.up_shares * summary.up_avg_cost + summary.down_shares * summary.down_avg_cost)
        locked_resting = sum(o.price * o.shares for o in list(active_orders.values()))
        if held_value + locked_resting >= self.window_capital_cap:
            return

        # Inventory Balance Cap (deadband) with hysteresis — REUSED unchanged.
        # As a maker we hold inventory, so this is the primary adverse-selection
        # defense: halt (and cancel) the heavy side until the other catches up.
        up, dn = summary.up_shares, summary.down_shares
        lean_up = (up - dn) / max(up, dn, 1)
        lean_dn = (dn - up) / max(up, dn, 1)

        if not self._is_paused_up and lean_up > 0.15 and (up - dn) > 10.0:
            self._is_paused_up = True
            for oid, o in list(active_orders.items()):
                if o.side == "UP":
                    await self._cancel_order(oid, active_orders)

        if not self._is_paused_down and lean_dn > 0.15 and (dn - up) > 10.0:
            self._is_paused_down = True
            for oid, o in list(active_orders.items()):
                if o.side == "DOWN":
                    await self._cancel_order(oid, active_orders)

        if self._is_paused_up and lean_up < 0.06:
            self._is_paused_up = False
        if self._is_paused_down and lean_dn < 0.06:
            self._is_paused_down = False

        # Fair-value split bids (capped below the asks).
        up_bid, down_bid = self._compute_maker_bids(yes_bid, no_bid, yes_ask, no_ask)
        if up_bid is None:
            return

        post_up = (
            not self._is_paused_up
            and summary.up_shares < self.farm_max_shares
            and (now - last_up_post_at) >= REQUOTE_INTERVAL_S
            and up_bid > 0
        )
        post_down = (
            not self._is_paused_down
            and summary.down_shares < self.farm_max_shares
            and (now - last_down_post_at) >= REQUOTE_INTERVAL_S
            and down_bid > 0
        )

        if post_up:
            await self._requote_side(
                "UP", market.yes_token_id, up_bid, down_bid, active_orders, summary
            )
        if post_down:
            await self._requote_side(
                "DOWN", market.no_token_id, down_bid, up_bid, active_orders, summary
            )

    async def _requote_side(
        self,
        side: str,
        token_id: str,
        my_target: float,
        other_target: float,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> None:
        """Re-quote one side: keep a well-priced resting bid, else cancel+repost.

        The bid is additionally capped against the OTHER side's *actual* resting
        price (not just this cycle's target) so a completed pair can never exceed
        TARGET_COMBINED, even when the kept order is a touch stale.
        """
        other_side = "DOWN" if side == "UP" else "UP"
        other_resting = max(
            (o.price for o in active_orders.values() if o.side == other_side),
            default=None,
        )
        budget_used = other_resting if other_resting is not None else other_target

        # Hard budget guarantee: my bid + the other leg <= TARGET_COMBINED.
        my_bid = round(min(my_target, TARGET_COMBINED - budget_used), 2)
        if my_bid <= 0:
            return

        # Re-quote discipline: keep the resting order if still well-priced
        # (preserving queue position); otherwise cancel and post fresh.
        existing = [(oid, o) for oid, o in list(active_orders.items()) if o.side == side]
        if any(abs(o.price - my_bid) <= STALE_DRIFT for _, o in existing):
            return
        for oid, _ in existing:
            await self._cancel_order(oid, active_orders)

        await self._post_quote(token_id, side, my_bid, active_orders, summary)

    async def _post_quote(
        self,
        token_id: str,
        side: str,
        price: float,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> bool:
        """Post a single resting GTC maker bid at `price`."""
        shares = self._dollar_target_shares(price)

        # Hard guard against the exchange 5-share minimum (avoids the reject flood).
        if shares < MIN_ORDER_SHARES:
            return False

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
            self.logger.debug(
                "Maker quote: %s %s @ $%.2f x%d", side, summary.market_id[:12], price, shares
            )
            return True
        except Exception as e:
            self.logger.debug("Maker quote error %s $%.2f: %s", side, price, e)
            return False


    # =========================================================================
    # Order management
    # =========================================================================

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

        # Matched fraction = 2·min(up,dn)/(up+dn): the share of inventory that is
        # paired (and thus mergeable / immune to adverse selection). gabagool ~94%;
        # a one-sided, adversely-selected window collapses toward 0%.
        up_sh, dn_sh = summary.up_shares, summary.down_shares
        total_sh = up_sh + dn_sh
        matched_frac = (2.0 * min(up_sh, dn_sh) / total_sh) if total_sh > 0 else 0.0

        self.logger.info(
            "[%02d:%02d] %s | %s | UP: %d sh @ $%.2f | DN: %d sh @ $%.2f | Comb: %s | Matched: %.0f%%",
            int(elapsed // 60), int(elapsed % 60),
            summary.market_id[:16],
            state.value,
            summary.up_shares, up_avg,
            summary.down_shares, dn_avg,
            c_str,
            matched_frac * 100.0,
        )
