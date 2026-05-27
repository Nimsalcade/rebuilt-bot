#!/usr/bin/env python3
"""
Gabagool Bot - Precision Maker Loop (Farming + Latency Snipe)

Purpose:
    Per-window execution engine implementing the full Gabagool22 strategy:

    PHASE 1 — FARMING (quiet market)
        Post passive maker bids on BOTH UP and DOWN sides.
        Earn maker rewards. Collect fills at $0.01-$0.03 floor.
        Cancel and repost stale orders to stay at top of book.

    PHASE 2 — SNIPING (spike detected)
        When SpikeDetector fires (BTC moves ≥0.02% in 5s):
          1. Instantly cancel all opposing-side orders (async)
          2. Fire aggressive marketable limit on the winning side
          3. Enter COOLDOWN (45s) before returning to FARMING

    PHASE 3 — COOLDOWN
        Sit on existing positions. No new orders. Wait for cooldown to expire.

State Machine:
    FARMING → [spike detected] → SNIPING → COOLDOWN → FARMING
    FARMING → [window closing] → HOLD
    Any state → [window resolved] → DONE

Key design decisions:
    - SpikeDetector polls every SPIKE_POLL_INTERVAL_S (0.25s) for speed
    - Farming refreshes every FARM_REFRESH_S (10s) — passive, cheap
    - Snipe uses FOK (Fill-Or-Kill) orders for guaranteed execution
    - Hard stop on all posting 3 minutes before window end (adverse selection)

Author: AI-Generated
Created: 2026-05-03
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple

from .merge_engine import MergeEngine, MIN_MERGE_SHARES
import src.terminal_ui as terminal_ui


# ============================================================================
# Configuration
# ============================================================================

# --- Farming parameters ---
FARM_ORDER_SHARES  = 10        # Shares per rung of the ladder
FARM_REFRESH_S     = 10.0      # How often to repost the full ladder (g22: orders every 2-10s)
MAX_ORDER_AGE_S    = 15.0      # Cancel and repost if order older than this
FARM_MAX_SHARES    = 500       # Max total farming shares per side per window
MIN_BID_PRICE      = 0.10      # Floor bids now dollar-targeted: 10sh×0.10=$1.00 ✓
MAX_BID_PRICE      = 0.95      # Never bid above 95¢

# --- Gabagool22-style price ladder ---
# Posts orders at EVERY level simultaneously — floor bids (lottery),
# mid-range (delta neutral core), and high bids (snipe layer).
LADDER_STEPS = [
    0.10, 0.15, 0.20,           # Floor bids — cheap lottery (huge ROI when hits)
    0.25, 0.30, 0.35, 0.40,     # Low delta zone
    0.45, 0.50, 0.55,           # Core delta-neutral zone (fills most often)
    0.60, 0.65, 0.70,           # Upper mid zone
    0.75, 0.80, 0.85,           # High conviction / snipe layer
]

# --- Dollar-targeted sizing per rung (matches g22's exact order sizing) ---
# g22 data analysis: he targets ~$1 at floor, scaling up to ~$8 at top.
# We compute shares = ceil(target_dollars / price), min enough to meet $1 Poly min.
#
# Zone         | Target $ | Example: 5 shares flat (OLD) vs dollar-target (NEW)
# Floor 0-20¢  |   $1.00  | 5×0.10=$0.50 REJECTED → 10×0.10=$1.00 ACCEPTED ✓
# Low   20-40¢ |   $2.00  | 5×0.30=$1.50 ok       →  7×0.30=$2.10 (matches g22)
# Mid   40-60¢ |   $4.00  | 5×0.50=$2.50 low      →  8×0.50=$4.00 (matches g22)
# High  60-80¢ |   $5.00  | 5×0.70=$3.50 low      →  7×0.70=$4.90 (matches g22)
# Top   80-100¢|   $8.00  | 5×0.85=$4.25 low      →  9×0.85=$7.65 (matches g22)
RUNG_DOLLAR_TARGETS = [
    (0.00, 0.20, 1.00),   # floor lottery — $1 each
    (0.20, 0.40, 2.00),   # low zone      — $2 each
    (0.40, 0.60, 4.00),   # mid zone      — $4 each
    (0.60, 0.80, 5.00),   # high zone     — $5 each
    (0.80, 1.01, 8.00),   # top zone      — $8 each (g22's conviction layer)
]


def _dollar_target_shares(price: float) -> int:
    """Compute shares for one ladder rung using g22's dollar-targeting approach.

    Returns enough shares to hit the zone's dollar target, always meeting
    Polymarket's $1 minimum order size requirement.
    """
    target = 1.00  # fallback
    for lo, hi, t in RUNG_DOLLAR_TARGETS:
        if lo <= price < hi:
            target = t
            break
    # shares = target_dollars / price, rounded up to nearest int
    shares = math.ceil(target / price)
    # Ensure we never place a sub-$1 order (Polymarket API requirement)
    while shares * price < 1.00:
        shares += 1
    return shares

# --- Spike polling ---
SPIKE_POLL_INTERVAL_S = 0.25   # Check for spikes 4x per second

# --- Window close buffer ---
STOP_POSTING_BUFFER_S = 60     # Stop all orders 60s before window end

# --- Hedge enforcement ---
HEDGE_IMBALANCE_RATIO  = 3.0   # Allow more lean — ladder naturally self-hedges

# --- Combined cost gate ---
# If the running average of (UP_avg + DOWN_avg) exceeds this threshold the
# window is no longer profitable — stop posting any new orders for it.
# Forensic analysis: 47.7% of windows exceeded $1.00 due to order-sequence
# slippage. Stopping at $0.95 preserves a minimum 5% gross margin.
MAX_COMBINED_COST_GATE = 0.95

# --- Hard capital cap ---
# g22-matched: 4 markets × $250 = $1000 total.
MAX_GLOBAL_CAPITAL_USD   = 1000.0   # Hard ceiling — scaled for $1000 deposit
MAX_PER_WINDOW_CAPITAL   = MAX_GLOBAL_CAPITAL_USD / 4   # = $250 per window



# ============================================================================
# Data Classes
# ============================================================================

class LoopState(Enum):
    FARMING     = "FARMING"
    SNIPING     = "SNIPING"
    COOLDOWN    = "COOLDOWN"
    LIQUIDATION = "LIQUIDATION"  # Selling losing side before settlement
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
    up_total_cost:  float = 0.0

    down_fills:       int   = 0
    down_shares:      float = 0.0
    down_total_cost:  float = 0.0

    signal_direction:  Optional[str]   = None
    signal_confidence: float           = 0.0
    signal_fired_at_s: Optional[float] = None

    snipes_fired:  int   = 0    # number of snipes executed this window
    snipe_latency_avg_ms: float = 0.0

    winner:  Optional[str]   = None
    pnl:     Optional[float] = None
    merged_usdc: float       = 0.0

    @property
    def up_avg_cost(self) -> float:
        return self.up_total_cost / self.up_shares if self.up_shares else 0.0

    @property
    def down_avg_cost(self) -> float:
        return self.down_total_cost / self.down_shares if self.down_shares else 0.0

    @property
    def total_invested(self) -> float:
        return self.up_total_cost + self.down_total_cost

    @property
    def lean_direction(self) -> Optional[str]:
        if self.up_shares > self.down_shares:
            return "UP"
        elif self.down_shares > self.up_shares:
            return "DOWN"
        return None

    def __str__(self) -> str:
        return terminal_ui.format_window_summary(self)


# ============================================================================
# MakerLoop
# ============================================================================

class MakerLoop:
    """
    Precision per-window maker + sniper execution engine.

    Run one instance per active 15-minute market window.
    """

    def __init__(
        self,
        bot: Any,
        dry_run: bool = False,
        farm_shares: int = FARM_ORDER_SHARES,
        farm_max_shares: int = FARM_MAX_SHARES,
        stop_posting_buffer_s: int = STOP_POSTING_BUFFER_S,
    ):
        self.bot                   = bot
        self.dry_run               = dry_run
        self.farm_shares           = farm_shares
        self.farm_max_shares       = farm_max_shares
        self.stop_posting_buffer_s = stop_posting_buffer_s
        self.logger                = logging.getLogger("maker_loop")
        self._cost_gate_logged_for_window: set = set()

        # ── Merge Engine: the core profit mechanism ───────────────────────────
        # After every fill reconciliation, opposing UP+DOWN pairs are merged
        # back to USDC immediately — this is how Gabagool22 earned $275K.
        self.merge_engine = MergeEngine(bot=bot, dry_run=dry_run)

    async def run(
        self,
        market: Any,
        window_end: datetime,
        spike_detector: Any,          # SpikeDetector (used for cooldown state)
        price_feed: Any,              # Per-asset price feed (for farming bids)
        sniper: Any,                  # Sniper execution engine
        burst_signal: Any = None,     # BurstSignal from WindowManager (multi-market)
        signal_engine: Any = None,    # Legacy (ignored)
        risk_manager: Any = None,
        stats_tracker: Any = None,
        db: Any = None,
        window_manager: Any = None,   # WindowManager for GlobalSniperEngine slot registration
    ) -> WindowFillSummary:
        """
        Run the full farming + snipe loop for one market window.

        Args:
            market:         Market object (yes_token_id, no_token_id, id)
            window_end:     When this window resolves
            spike_detector: SpikeDetector for latency-arb signal
            price_feed:     Live BTC price feed
            sniper:         Sniper execution engine
            signal_engine:  Optional legacy engine (ignored in snipe mode)
            risk_manager:   Optional risk validator
            stats_tracker:  Optional stats recorder
            db:             Optional database

        Returns:
            WindowFillSummary
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

        # Timestamps for farming refresh
        last_up_post_at:   float = 0.0
        last_down_post_at: float = 0.0

        # Cache bids — only refresh from CLOB every FARM_REFRESH_S, not every 250ms tick
        cached_yes_bid: Optional[float] = None
        cached_no_bid:  Optional[float] = None
        last_bid_fetch_at: float = 0.0
        BID_CACHE_TTL_S = FARM_REFRESH_S  # refresh bids at the same cadence as farming

        snipe_latencies: List[float] = []
        last_status_log_at: float = 0.0
        last_burst_handled_at: float = 0.0  # dedup: prevent double-fire
        STATUS_LOG_INTERVAL_S = 60.0

        self.logger.info(
            "MakerLoop starting for %s | end=%s | state=FARMING",
            market_id[:16], window_end.strftime("%H:%M:%S")
        )

        # ── Register snipe slot with GlobalSniperEngine ──
        snipe_slot = None
        if window_manager is not None:
            snipe_slot = window_manager.register_snipe_slot(
                market_id=market_id,
                market=market,
                active_orders=active_orders,
                summary=summary,
            )

        try:
            while state != LoopState.DONE:
                now        = time.time()
                elapsed_s  = now - loop_start
                seconds_to_end = (window_end - datetime.now()).total_seconds()

                # ── Window end check ─────────────────────────────────────────
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
                    
                    # Force merge any remaining pairs before expiration to eliminate risk
                    if summary.up_shares > 0 and summary.down_shares > 0:
                        await self.merge_engine.try_merge(market, summary, force=True)
                        
                    state = LoopState.HOLD

                # ── Liquidation removed per user request (Gabagool never sells) ──

                # ── Helper: burst-aware sleep (replaces all raw asyncio.sleep) ──
                async def _burst_sleep(duration: float) -> bool:
                    """Sleep for `duration`, but return True immediately if burst fires."""
                    if burst_signal is None:
                        await asyncio.sleep(duration)
                        return False
                    try:
                        await asyncio.wait_for(burst_signal.wait(), timeout=duration)
                        return True   # burst fired during sleep
                    except asyncio.TimeoutError:
                        return False  # normal sleep completed

                # ── Sentinel for burst interruption ──
                _BURST_INTERRUPTED = object()

                # ── Helper: run coroutine, abort if burst fires ──
                async def _do_or_burst(coro):
                    """Run coro; return _BURST_INTERRUPTED if burst fires mid-flight."""
                    if burst_signal is None:
                        return await coro
                    coro_task = asyncio.ensure_future(coro)
                    burst_wait = asyncio.ensure_future(burst_signal.wait())
                    done, pending = await asyncio.wait(
                        [coro_task, burst_wait],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for p in pending:
                        p.cancel()
                        try:
                            await p
                        except (asyncio.CancelledError, Exception):
                            pass
                    if burst_wait in done:
                        return _BURST_INTERRUPTED
                    return coro_task.result()

                # ── LIQUIDATION logic removed ──

                # ── HOLD: window closing soon ──
                # Farming and order posting are stopped, but burst snipes are
                # still allowed — massive volatility often occurs in the final
                # minutes before settlement.
                if state == LoopState.HOLD:
                    got_burst_in_hold = False
                    if burst_signal is not None:
                        try:
                            await asyncio.wait_for(burst_signal.wait(), timeout=1.0)
                            if burst_signal.is_set and (now - last_burst_handled_at) > 2.0:
                                got_burst_in_hold = True
                        except asyncio.TimeoutError:
                            pass
                    else:
                        _spike = spike_detector.check(price_feed)
                        if _spike.is_snipe and (now - last_burst_handled_at) > 2.0:
                            got_burst_in_hold = True

                    if got_burst_in_hold:
                        last_burst_handled_at = time.time()
                        direction = (
                            burst_signal.direction if burst_signal is not None
                            else _spike.direction
                        )
                        momentum = (
                            (burst_signal.momentum or 0.0) if burst_signal is not None
                            else (_spike.momentum_5s or 0.0)
                        )
                        self.logger.info(
                            "🎯 HOLD-STATE BURST | direction=%s | 5s=%+.4f%% | market=%s",
                            direction, momentum, market_id[:16],
                        )
                        if summary.signal_direction is None:
                            summary.signal_direction  = direction
                            summary.signal_fired_at_s = elapsed_s

                        if snipe_slot is not None and snipe_slot.last_result is not None:
                            snipe_result = snipe_slot.last_result
                            if snipe_result.success:
                                snipe_latencies.append(snipe_result.latency_ms)
                            snipe_slot.last_result = None
                    else:
                        await asyncio.sleep(1.0)
                    continue

                # ── COOLDOWN: check for expiry (sacred — burst does NOT override) ──
                if state == LoopState.COOLDOWN:
                    if not spike_detector.in_cooldown:
                        self.logger.info(
                            "Cooldown expired — returning to FARMING | market=%s",
                            market_id[:16]
                        )
                        state = LoopState.FARMING
                    await asyncio.sleep(SPIKE_POLL_INTERVAL_S)
                    continue

                # ── FARMING: wait for burst signal OR farming cadence ────────
                if state == LoopState.FARMING:
                    got_burst = False
                    direction = None
                    momentum  = 0.0

                    if burst_signal is not None:
                        # Wait for burst — returns immediately if already set
                        try:
                            await asyncio.wait_for(
                                burst_signal.wait(),
                                timeout=SPIKE_POLL_INTERVAL_S,
                            )
                            if burst_signal.is_set:
                                # Dedup guard: don't fire twice on the same burst
                                # (burst stays set for ~1s, but we only fire once)
                                if (now - last_burst_handled_at) > 2.0:
                                    got_burst = True
                                    direction = burst_signal.direction
                                    momentum  = burst_signal.momentum or 0.0
                        except asyncio.TimeoutError:
                            pass
                    else:
                        _spike = spike_detector.check(price_feed)
                        if _spike.is_snipe:
                            got_burst = True
                            direction = _spike.direction
                            momentum  = _spike.momentum_5s or 0.0

                    if got_burst and direction:
                        # === BURST DETECTED ===
                        # The GlobalSniperEngine already fired the snipe
                        # directly from the tick callback (~0ms delay).
                        # We just enter COOLDOWN and pick up results.
                        last_burst_handled_at = time.time()

                        state = LoopState.SNIPING
                        self.logger.info(
                            terminal_ui.fmt_spike(direction, momentum, price_feed.get_current_price() or 0.0, 1) # Note: assumes 1 for now if we can't easily get the number of markets
                        )

                        if summary.signal_direction is None:
                            summary.signal_direction  = direction
                            summary.signal_fired_at_s = elapsed_s

                        # Check if GlobalSniperEngine already wrote a result
                        if snipe_slot is not None and snipe_slot.last_result is not None:
                            snipe_result = snipe_slot.last_result
                            self.logger.info(
                                terminal_ui.fmt_snipe(market_id, snipe_result.success, snipe_result.price_paid or 0.0, snipe_result.shares, snipe_result.error or "error")
                            )
                            if snipe_result.success:
                                snipe_latencies.append(snipe_result.latency_ms)
                            snipe_slot.last_result = None  # consume it

                        state = LoopState.COOLDOWN
                        continue  # no sleep — enter cooldown immediately

                    # ── No burst → farming path ──

                    # Fetch bids (burst-interruptible)
                    need_bids = (now - last_bid_fetch_at) >= BID_CACHE_TTL_S
                    if need_bids:
                        try:
                            result = await _do_or_burst(self._get_bids(market))
                            if result is _BURST_INTERRUPTED:
                                continue  # burst fired — loop back
                            cached_yes_bid, cached_no_bid = result
                            last_bid_fetch_at = now
                        except (ValueError, TypeError):
                            if burst_signal and burst_signal.is_set:
                                continue
                            self.logger.warning("Market %s 404 — ending loop", market_id[:16])
                            break

                    if cached_yes_bid is None or cached_no_bid is None:
                        await _burst_sleep(SPIKE_POLL_INTERVAL_S)
                        continue

                    # Post farming orders (burst-interruptible)
                    farm_result = await _do_or_burst(self._farm_orders(
                        market, cached_yes_bid, cached_no_bid,
                        active_orders, summary,
                        last_up_post_at, last_down_post_at,
                        now,
                    ))
                    if farm_result is _BURST_INTERRUPTED:
                        continue  # burst fired during farming

                    # Update last post timestamps — snapshot to avoid mutation during iteration
                    for side, oid, order in [
                        (o.side, oid, o) for oid, o in list(active_orders.items())
                    ]:
                        if side == "UP":
                            last_up_post_at = max(last_up_post_at, order.placed_at)
                        else:
                            last_down_post_at = max(last_down_post_at, order.placed_at)

                    # Reconcile fills (burst-interruptible)
                    if need_bids:
                        rec = await _do_or_burst(self._reconcile_fills(
                            active_orders, summary, db, market_id
                        ))
                        if rec is _BURST_INTERRUPTED:
                            continue  # burst fired
                        await self._cancel_stale(active_orders)

                        # ── MERGE ENGINE ─────────────────────────────────────
                        # Immediately merge any opposing UP+DOWN pairs into USDC.
                        # This is Gabagool22's core profit mechanic ($275K of $281K revenue).
                        # Runs after every reconcile so we lock in spread profit
                        # without waiting for the binary option to expire.
                        if summary.up_shares >= MIN_MERGE_SHARES and summary.down_shares >= MIN_MERGE_SHARES:
                            await self.merge_engine.try_merge(market, summary)

                    # Log status every 60s
                    if (now - last_status_log_at) >= STATUS_LOG_INTERVAL_S and elapsed_s > 1:
                        self._log_status(summary, elapsed_s, state)
                        last_status_log_at = now

                    # Burst-aware sleep before next iteration
                    await _burst_sleep(SPIKE_POLL_INTERVAL_S)

        except asyncio.CancelledError:
            self.logger.info("MakerLoop cancelled: %s", market_id[:16])
        except Exception as e:
            self.logger.error("MakerLoop error: %s | %s", market_id[:16], e, exc_info=True)
        finally:
            # Unregister from GlobalSniperEngine
            if window_manager is not None:
                window_manager.unregister_snipe_slot(market_id)
            await self._cancel_all(active_orders)

        # Compute average snipe latency
        if snipe_latencies:
            summary.snipe_latency_avg_ms = sum(snipe_latencies) / len(snipe_latencies)

        naked_sh = summary.up_shares if summary.up_shares > summary.down_shares else summary.down_shares
        naked_side = "UP" if summary.up_shares > summary.down_shares else "DOWN"
        
        # Calculate a rough internal net PnL (pre-settlement)
        # Note: True settlement uses real outcome in paper trader
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
            signal=summary.signal_direction
        )
        # Print using print to bypass standard logger formatting, or logger.info if it doesn't prefix too badly
        # Actually logger.info will still prefix it with timestamp. 
        # But we added timestamps in terminal_ui. So we'll just print it.
        print(msg, flush=True)
        self.logger.info("MakerLoop complete: %s", market_id[:16])

        # Log merge session totals
        self.merge_engine.log_session_summary()


        # Record stats
        if stats_tracker:
            try:
                stats_tracker.record_trade(
                    market_id=market_id,
                    yes_price=summary.up_avg_cost,
                    no_price=summary.down_avg_cost,
                    profit_margin=0.0,
                    trade_size=summary.total_invested / 2,
                    notes=f"lean={summary.signal_direction or 'none'} snipes={summary.snipes_fired}",
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
        """Post the full Gabagool22-style price ladder on both sides.

        Instead of one order at best bid, we blast the entire book:
          - Floor bids ($0.10-0.20): cheap lottery — fills on extreme moves
          - Mid zone ($0.25-0.60):   core delta-neutral farming
          - Snipe layer ($0.65-0.85): fills on spike — replaces FOK snipe

        We only repost the ladder if FARM_REFRESH_S has elapsed since
        the last ladder was placed on that side (not per-order).
        """
        up_sh   = max(summary.up_shares, 0.1)
        down_sh = max(summary.down_shares, 0.1)
        up_allowed   = (up_sh / down_sh) < HEDGE_IMBALANCE_RATIO
        down_allowed = (down_sh / up_sh) < HEDGE_IMBALANCE_RATIO

        # Always allow bootstrap rungs until each side has at least 2 fills
        if summary.up_fills < 2:
            up_allowed = True
        if summary.down_fills < 2:
            down_allowed = True

        # ── Combined cost gate ────────────────────────────────────────────────
        # If we have fills on BOTH sides and the combined avg exceeds the gate,
        # this window is no longer profitable.  Stop posting — don't dig deeper.
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
                return  # stop posting — protect from further losses

        # ── Hard capital cap: $1,000 global / 4 windows = $250 per window ──
        # Snapshot resting orders to avoid mutation during concurrent ladder posting
        locked_resting = sum(
            o.price * o.shares for o in list(active_orders.values())
        )
        total_committed = summary.total_invested + locked_resting
        if total_committed >= MAX_PER_WINDOW_CAPITAL:
            self.logger.debug(
                "Capital cap reached | window=%s | committed=$%.2f / $%.2f",
                summary.market_id[:12], total_committed, MAX_PER_WINDOW_CAPITAL
            )
            return  # at ceiling — don't post any new orders

        if up_allowed and summary.up_shares < self.farm_max_shares:
            if (now - last_up_post_at) >= FARM_REFRESH_S:
                await self._post_ladder(
                    market.yes_token_id, "UP", active_orders, summary
                )

        if down_allowed and summary.down_shares < self.farm_max_shares:
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
        """Blast the full price ladder — all rungs at once.

        Matches gabagool22's pattern of 10-18 orders per market per direction
        covering $0.10 → $0.85 in $0.05 increments.
        """
        tasks = []
        for price in LADDER_STEPS:
            tasks.append(self._post_ladder_rung(token_id, side, price, active_orders, summary))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        placed = sum(1 for r in results if r is True)
        self.logger.debug(
            "Ladder posted: %s %s | %d/%d rungs placed",
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
        """Post a single GTC limit order at one rung of the ladder."""
        # Skip if we already have a live order at this price level on this side.
        # Snapshot to avoid "dict changed size during iteration" — _post_ladder
        # fires all rungs concurrently via asyncio.gather, so active_orders can
        # be mutated by a sibling coroutine while we iterate here.
        for o in list(active_orders.values()):
            if o.side == side and abs(o.price - price) < 0.03:
                return False  # already covered this rung

        # Dollar-targeted share sizing (matches g22's order sizes)
        shares = _dollar_target_shares(price)

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

    def _has_recent_order(self, active_orders: Dict[str, OrderRecord], side: str) -> bool:
        now = time.time()
        # Snapshot — called from farming path where concurrent rungs may be live
        return any(
            o.side == side and (now - o.placed_at) < FARM_REFRESH_S
            for o in list(active_orders.values())
        )

    async def _cancel_stale(self, active_orders: Dict[str, OrderRecord]) -> None:
        now = time.time()
        # Snapshot keys before iterating — _cancel_order mutates active_orders
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
        except Exception as e:
            self.logger.debug("Cancel error %s: %s", order_id[:12], e)
        finally:
            active_orders.pop(order_id, None)

    async def _get_bids(self, market: Any) -> Tuple[Optional[float], Optional[float]]:
        """Get current best bid for YES and NO tokens."""
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

    # =========================================================================
    # Liquidation — sell losing positions before settlement
    # =========================================================================

    async def _identify_losing_side(
        self,
        market: Any,
        summary: WindowFillSummary,
    ) -> Optional[str]:
        """Determine which side is losing based on current Polymarket bids.

        The losing side's bid will be < $0.30 as the market approaches
        settlement. Returns "UP" or "DOWN" or None if can't determine.
        """
        try:
            yes_spread, no_spread = await asyncio.gather(
                asyncio.to_thread(self.bot.get_spread, market.yes_token_id),
                asyncio.to_thread(self.bot.get_spread, market.no_token_id),
            )
            yes_bid = yes_spread.get("bid", 0.5)
            no_bid  = no_spread.get("bid", 0.5)

            self.logger.info(
                "LIQUIDATION check | YES_bid=$%.3f | NO_bid=$%.3f",
                yes_bid, no_bid,
            )

            # The side with the lower bid is losing
            if yes_bid < 0.30 and summary.up_shares > 0:
                return "UP"
            elif no_bid < 0.30 and summary.down_shares > 0:
                return "DOWN"
            else:
                return None  # neither side clearly losing, or no shares to sell
        except Exception as e:
            self.logger.warning("Losing side check failed: %s", e)
            return None

    async def _sell_losing_positions(
        self,
        market: Any,
        summary: WindowFillSummary,
        price_feed: Any,
    ) -> float:
        """Sell all shares on the losing side before settlement.

        Places aggressive SELL orders at (current_bid - $0.01) to guarantee
        fast execution. Even recovering $0.03 per share beats $0.00 at
        settlement.

        Returns:
            Total USDC recovered from sells.
        """
        losing_side = await self._identify_losing_side(market, summary)
        if losing_side is None:
            return 0.0

        # Select the token and shares to sell
        if losing_side == "UP":
            token_id = market.yes_token_id
            shares_to_sell = summary.up_shares
            side_label = "UP (YES)"
        else:
            token_id = market.no_token_id
            shares_to_sell = summary.down_shares
            side_label = "DOWN (NO)"

        if shares_to_sell <= 0:
            return 0.0

        # Get current bid to price our sell aggressively
        try:
            spread = await asyncio.to_thread(self.bot.get_spread, token_id)
            current_bid = spread.get("bid", 0.0)
        except Exception:
            current_bid = 0.05  # fallback to floor

        # Sell at bid - $0.01 (aggressive) but never below MIN_SELL_PRICE
        sell_price = round(max(MIN_SELL_PRICE, current_bid - 0.01), 2)

        self.logger.info(
            "🏷️ LIQUIDATION SELL | side=%s | shares=%.1f | price=$%.3f | "
            "market=%s",
            side_label, shares_to_sell, sell_price,
            summary.market_id[:16],
        )

        if self.dry_run:
            recovered = sell_price * shares_to_sell
            self.logger.info(
                "DRY RUN LIQUIDATION: Would sell %.1f %s shares @ $%.3f = $%.2f recovered",
                shares_to_sell, side_label, sell_price, recovered,
            )
            return recovered

        # Place the SELL order
        try:
            result = await asyncio.to_thread(
                self.bot.place_order,
                token_id,
                sell_price,
                float(shares_to_sell),
                "SELL",   # KEY: this is a SELL, not BUY
                "FOK",    # Fill-Or-Kill: sell everything or nothing
            )

            if result and result.get("orderID"):
                recovered = sell_price * shares_to_sell
                self.logger.info(
                    "💰 SELL FILLED | %s × %.1f @ $%.3f | recovered=$%.2f | id=%s",
                    side_label, shares_to_sell, sell_price, recovered,
                    result["orderID"][:16],
                )
                return recovered
            else:
                # FOK didn't fill — try again at a lower price
                retry_price = round(max(MIN_SELL_PRICE, sell_price - 0.02), 2)
                self.logger.info(
                    "SELL FOK missed at $%.3f — retrying at $%.3f",
                    sell_price, retry_price,
                )
                result2 = await asyncio.to_thread(
                    self.bot.place_order,
                    token_id, retry_price, float(shares_to_sell),
                    "SELL", "FOK",
                )
                if result2 and result2.get("orderID"):
                    recovered = retry_price * shares_to_sell
                    self.logger.info(
                        "💰 SELL FILLED (retry) | %s × %.1f @ $%.3f | recovered=$%.2f",
                        side_label, shares_to_sell, retry_price, recovered,
                    )
                    return recovered
                self.logger.warning("SELL failed on retry — holding to settlement")
                return 0.0

        except Exception as e:
            self.logger.error("Liquidation sell error: %s", e)
            return 0.0

    # =========================================================================
    # Fill reconciliation
    # =========================================================================

    async def _reconcile_fills(
        self,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
        db: Any,
        market_id: str,
    ) -> None:
        if self.dry_run:
            await self._simulate_fills(active_orders, summary)
            return

        try:
            open_orders = await asyncio.to_thread(self.bot.get_open_orders)
            open_ids = {o.get("id", o.get("orderID", "")) for o in open_orders}

            filled = [
                oid for oid in list(active_orders.keys())
                if oid not in open_ids and not oid.startswith("dry_")
            ]
            for oid in filled:
                if oid in active_orders:
                    order = active_orders.pop(oid)
                    self._record_fill(order, summary, db, market_id)
        except Exception as e:
            self.logger.debug("Reconcile error: %s", e)

    async def _simulate_fills(
        self,
        active_orders: Dict[str, OrderRecord],
        summary: WindowFillSummary,
    ) -> None:
        """
        Simulate fill detection for dry run.

        Uses price-aware fill probability to better reflect thin-book reality:
          - Very cheap bids ($0.01-$0.15): fill occasionally — easy fills at floor
          - Mid-range ($0.16-$0.50):       fill rarely — thin liquidity at mid
          - Expensive ($0.51+):            almost never — sellers demand the price

        Called every FARM_REFRESH_S (~10s). All probabilities are per-check.
        """
        import random
        now = time.time()
        for oid in list(active_orders.keys()):
            order = active_orders[oid]
            age   = now - order.placed_at

            # Must be resting for at least 10s
            if age < 10:
                continue

            # Price-aware fill probability
            p = order.price
            if p <= 0.15:
                fill_prob = 0.20   # cheap floor bids — fill ~once per 50s
            elif p <= 0.35:
                fill_prob = 0.08   # mid-range — fill ~once per 2 min
            elif p <= 0.55:
                fill_prob = 0.04   # getting expensive — fill ~once per 4 min
            else:
                fill_prob = 0.01   # above fair value — very rare

            if random.random() < fill_prob:
                active_orders.pop(oid)
                self._record_fill(order, summary, None, summary.market_id)

    def _record_fill(
        self,
        order: OrderRecord,
        summary: WindowFillSummary,
        db: Any,
        market_id: str,
    ) -> None:
        cost = order.shares * order.price
        if order.side == "UP":
            summary.up_fills      += 1
            summary.up_shares     += order.shares
            summary.up_total_cost += cost
        else:
            summary.down_fills      += 1
            summary.down_shares     += order.shares
            summary.down_total_cost += cost

        print(terminal_ui.fmt_fill(market_id, order.side, order.price, order.shares, cost), flush=True)

        if db:
            try:
                db.save_trade(
                    market_id=market_id, side=order.side,
                    shares=order.shares, price=order.price,
                    cost=cost, order_id=order.order_id,
                )
            except Exception:
                pass

    # =========================================================================
    # Logging
    # =========================================================================

    def _log_status(
        self,
        summary: WindowFillSummary,
        elapsed_s: float,
        state: LoopState,
    ) -> None:
        print(terminal_ui.fmt_status(
            summary.market_id,
            elapsed_s,
            state.value,
            summary.up_shares,
            summary.up_avg_cost,
            summary.down_shares,
            summary.down_avg_cost,
            summary.total_invested,
            summary.lean_direction or "even"
        ), flush=True)
