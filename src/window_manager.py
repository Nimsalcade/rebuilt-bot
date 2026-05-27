#!/usr/bin/env python3
"""
Gabagool Bot - Window Manager (Multi-Window Lifecycle Orchestration)

Purpose:
    Discovers active 15-minute windows for BTC/ETH/SOL and manages
    concurrent MakerLoop sessions. Handles window open detection,
    concurrent session limits, and graceful shutdown.

    KEY: Runs a single centralised spike monitor that broadcasts a
    burst event to ALL active MakerLoops simultaneously when a spike
    fires. This replicates Gabagool22's observed 3-5 order bursts
    across multiple markets in the same 2-second window.

Author: AI-Generated
Created: 2026-05-03

Usage:
    manager = WindowManager(
        bot=bot,
        spike_detector=spike_detector,
        price_feeds=feed_manager,
        sniper=sniper,
        config=config,
    )
    await manager.run_forever()
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, Optional, Any, List

from .maker_loop import MakerLoop, WindowFillSummary
from .gamma_client import GammaClient, Market
from .sniper import SnipeResult
from .capital_manager import CapitalManager


# How often to scan for new windows (seconds)
DISCOVERY_INTERVAL_S = 30

# How many seconds after a window opens to start the maker loop
WINDOW_START_DELAY_S = 30

# Max concurrent active sessions across all assets
# g22-matched: BTC+ETH × (5m + 15m + 1h) = 6 markets simultaneously
MAX_CONCURRENT_SESSIONS = 6

# Max concurrent 1-hour window sessions.
# 1 out of 6 slots = ~17% capacity for 1h, leaving 83% for 15-minute windows.
# This enforces the 80-90% weighting toward 15-minute windows.
MAX_1H_SESSIONS = 1

# How long the burst event stays SET (gives all loops time to wake & read it)
BURST_EVENT_HOLD_S = 1.0


@dataclass
class SnipeSlot:
    """Everything the GlobalSniperEngine needs to fire a snipe for one market.

    Registered by each MakerLoop when it enters FARMING state.
    The WindowManager fires snipes directly through these slots,
    bypassing the MakerLoop's event-loop wake penalty.
    """
    market_id: str
    market: Any              # Market object (yes/no token IDs)
    active_orders: Dict      # mutable ref — shared with MakerLoop
    summary: Any             # WindowFillSummary — shared with MakerLoop
    last_result: Optional[SnipeResult] = None   # written by GlobalSniper, read by MakerLoop
    fired_at: float = 0.0    # timestamp of last global snipe for this slot


class BurstSignal:
    """
    Shared broadcast state for multi-market burst snipes.

    The spike monitor sets this when a spike is detected; all MakerLoops
    read from it to fire their snipes in the same asyncio iteration.
    """

    def __init__(self):
        self._event: asyncio.Event = asyncio.Event()
        self.direction: Optional[str] = None
        self.momentum: Optional[float] = None
        self.asset: Optional[str] = None

    def fire(self, direction: str, momentum: float, asset: str) -> None:
        """Set the burst signal — wakes all waiting MakerLoops."""
        self.direction = direction
        self.momentum  = momentum
        self.asset     = asset
        self._event.set()

    def clear(self) -> None:
        """Reset after all loops have had a chance to read."""
        self._event.clear()
        self.direction = None

    async def wait(self) -> None:
        """Await the next burst signal."""
        await self._event.wait()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()


class WindowSession:
    """Tracks a single active window + its MakerLoop task."""

    def __init__(self, market: Market, task: asyncio.Task):
        self.market = market
        self.task = task
        self.started_at = datetime.now()
        self.summary: Optional[WindowFillSummary] = None
        self.completed = False

    @property
    def market_id(self) -> str:
        return self.market.id

    @property
    def age_seconds(self) -> float:
        return (datetime.now() - self.started_at).total_seconds()


class WindowManager:
    """
    Discovers and manages concurrent 15-minute window sessions.

    Lifecycle per window:
    1. Gamma API signals window is open and accepting orders
    2. WindowManager waits WINDOW_START_DELAY_S seconds
    3. Starts MakerLoop (FARMING + BURST SNIPE state machine) as a task
    4. MakerLoop runs until window closes (~14.5 min mark)
    5. Session is cleaned up, results queued for paper settlement

    Burst Snipe Architecture:
    - A single _run_spike_monitor() task watches the BTC price feed
    - On spike: fires BurstSignal (asyncio.Event) → ALL active MakerLoops
      wake simultaneously → each executes its own market's snipe in parallel
    - This matches Gabagool22's observed 3-5 simultaneous orders per burst
    """

    def __init__(
        self,
        bot: Any,
        spike_detector: Any,        # SpikeDetector instance
        price_feeds: Any,           # PriceFeedManager
        sniper: Any,                # Sniper instance
        config: Any,                # Config object
        db: Any = None,
        risk_manager: Any = None,
        stats_tracker: Any = None,
        dry_run: bool = False,
        paper_trader: Any = None,
    ):
        self.bot             = bot
        self.spike_detector  = spike_detector
        self.price_feeds     = price_feeds
        self.sniper          = sniper
        self.config          = config
        self.db              = db
        self.risk_manager    = risk_manager
        self.stats_tracker   = stats_tracker
        self.dry_run         = dry_run
        self.paper_trader    = paper_trader

        self.logger = logging.getLogger("window_manager")
        self.gamma  = GammaClient()

        self._sessions: Dict[str, WindowSession] = {}
        self._seen_market_ids: Set[str] = set()
        self.running = False
        self.completed_windows: list = []

        # ── Burst snipe broadcast ─────────────────────────────────────────
        self._burst = BurstSignal()

        # ── Global Sniper Engine ──────────────────────────────────────────
        # MakerLoops register their snipe context here.
        # When a spike fires, we execute ALL snipes directly from the tick
        # callback via loop.create_task(), erasing the 114ms MakerLoop
        # wake penalty.
        self._snipe_slots: Dict[str, SnipeSlot] = {}
        self._last_global_snipe_at: float = 0.0
        self._global_snipe_dedup_s: float = 2.0  # dedup window

        # ── Capital Manager ───────────────────────────────────────────────────
        gabagool_cfg = getattr(config, 'gabagool', config)
        session_capital = getattr(gabagool_cfg, 'session_capital_usd', 100.0)
        auto_compound = getattr(gabagool_cfg, 'auto_compound_pct', 0.0)
        self._capital_mgr = CapitalManager(
            bot=bot,
            session_capital_usd=session_capital,
            auto_compound_pct=auto_compound,
            logger=logging.getLogger("capital_manager"),
            paper_trader=self.paper_trader,
        )

    # =========================================================================
    # Main Loop
    # =========================================================================

    async def run_forever(self) -> None:
        """Main discovery, spike monitor, and session management loop.

        CYCLE STRUCTURE (new — post-forensics)
        ──────────────────────────────────────
        Each "cycle" corresponds to one full pass of:
          start_cycle() → discover & run windows → end_cycle()

        CapitalManager enforces:
          - Only `session_capital_usd` is ever deployed per cycle.
          - No new windows start until all positions are fully settled.
          - If the returned USDC < session_capital, the bot halts.
        """
        self.running = True
        gabagool_cfg  = self.config.gabagool if hasattr(self.config, 'gabagool') else self.config
        target_assets = getattr(gabagool_cfg, 'target_assets', ['BTC', 'ETH', 'SOL'])

        self.logger.info(
            "WindowManager starting | assets=%s | dry_run=%s",
            target_assets, self.dry_run
        )

        # Wait for price feeds
        self.logger.info("Waiting for price feeds...")
        await self.price_feeds.wait_for_all_feeds(timeout=20)

        if not any(self.price_feeds.is_ready(a) for a in target_assets):
            self.logger.error("No price feeds available — cannot run")
            return

        self.logger.info("Price feeds ready. Starting window discovery.")

        # Register event-driven spike detection on BTC feed
        self._register_spike_callback()

        # Launch the burst-clear maintenance loop
        clear_task = asyncio.create_task(
            self._run_burst_clear_loop(),
            name="burst_clear_loop"
        )

        try:
            while self.running:
                # ── CYCLE GATE: ask CapitalManager if it's safe to trade ────
                cycle_ok = await self._capital_mgr.start_cycle()
                if not cycle_ok:
                    self.logger.critical(
                        "CapitalManager denied cycle start — shutting down."
                    )
                    self.running = False
                    break

                # ── INTRA-CYCLE: discover and run windows until none remain ─
                # We keep looping the discovery scan until the seen-market
                # set stops growing (i.e., the current crop of windows have
                # all been submitted as tasks).  Then we wait for ALL tasks
                # to complete before ending the cycle.
                cycle_seen_before = len(self._seen_market_ids)

                try:
                    await self._discovery_cycle()
                    await self._cleanup_completed_sessions()
                    self._log_status()

                    # If new sessions were started, keep scanning
                    new_sessions_started = len(self._seen_market_ids) > cycle_seen_before

                    if self._sessions:
                        # Active sessions running — poll frequently
                        await asyncio.sleep(DISCOVERY_INTERVAL_S)
                        continue

                    if new_sessions_started:
                        # We just submitted tasks — give them a moment to register
                        await asyncio.sleep(DISCOVERY_INTERVAL_S)
                        continue

                    # No active sessions and nothing new — cycle's windows are done.
                    # Wait for any still-completing tasks to drain.
                    if self._sessions:
                        await asyncio.sleep(5)
                        continue

                    # ── All windows done for this cycle — end it ────────────
                    await self._capital_mgr.end_cycle()
                    # Reset seen-market-ids so next cycle can pick up new windows
                    self._seen_market_ids.clear()

                    self.logger.info(
                        "Cycle complete. CapitalManager will wait for settlement "
                        "before next cycle starts."
                    )
                    # Small pause before the next cycle's start_cycle() settlement wait
                    await asyncio.sleep(5)

                except asyncio.CancelledError:
                    self.logger.info("WindowManager cancelled")
                    break
                except Exception as e:
                    self.logger.error("Discovery error: %s", e, exc_info=True)
                    await asyncio.sleep(10)
        finally:
            clear_task.cancel()
            await self._shutdown_all_sessions()
            self.logger.info("WindowManager stopped")


    # =========================================================================
    # Centralised Spike Monitor (EVENT-DRIVEN — zero polling latency)
    # =========================================================================

    def _register_spike_callback(self) -> None:
        """
        Register a synchronous callback on the BTC price feed.

        This is called by the WebSocket on_message handler on EVERY aggTrade
        tick — typically every 10-50ms at BTC peak volume. There is zero
        polling delay: the spike detector runs inline in the same stack
        frame as the price update.

        If a spike is detected, it fires the BurstSignal (sets the
        asyncio.Event) so all waiting MakerLoops wake in the same event
        loop iteration.
        """
        btc_feed = self.price_feeds.feeds.get("BTC")
        if btc_feed is None:
            self.logger.error("No BTC price feed — spike monitor cannot start")
            return

        def _on_tick(feed):
            """Runs synchronously inside the WebSocket message handler.

            GlobalSniperEngine: when a spike fires, we:
            1. Set the BurstSignal (tells MakerLoops to enter COOLDOWN)
            2. Schedule _global_snipe_burst() as a task (fires ALL snipes
               in <1ms, bypassing MakerLoop wake penalty)
            """
            if self._burst.is_set:
                return

            result = self.spike_detector.check(feed)
            if not result.is_snipe:
                return

            # Dedup: don't fire twice within the dedup window
            now = time.time()
            if (now - self._last_global_snipe_at) < self._global_snipe_dedup_s:
                return
            self._last_global_snipe_at = now

            # Collect registered snipe slots
            slots = [s for s in self._snipe_slots.values()]
            if not slots:
                return

            direction = result.direction
            momentum  = result.momentum_5s or 0.0

            self.logger.info(
                "📡 BURST BROADCAST | direction=%s | 5s=%+.4f%% | "
                "firing %d markets via GlobalSniperEngine",
                direction, momentum, len(slots)
            )

            # 1. Set burst signal (MakerLoops use this for COOLDOWN transition)
            self._burst.fire(
                direction=direction,
                momentum=momentum,
                asset="BTC",
            )

            # 2. Schedule the global snipe burst — fires on the NEXT event
            #    loop iteration (<1ms), not after MakerLoops wake (114ms).
            loop = asyncio.get_event_loop()
            loop.create_task(
                self._global_snipe_burst(direction, momentum, slots)
            )

        btc_feed.register_on_tick(_on_tick)
        self.logger.info(
            "Spike monitor registered (GlobalSniperEngine, zero-wake-latency on BTC aggTrade)"
        )

    async def _run_burst_clear_loop(self) -> None:
        """
        Simple maintenance loop: clears the burst event after HOLD duration.

        The burst signal needs to stay SET long enough for all MakerLoops
        to wake and read it (they're all awaiting burst_signal.wait()).
        After BURST_EVENT_HOLD_S we clear it so the next spike can fire.
        """
        while self.running:
            try:
                if self._burst.is_set:
                    await asyncio.sleep(BURST_EVENT_HOLD_S)
                    self._burst.clear()
                else:
                    await asyncio.sleep(0.05)  # 50ms check cadence
            except asyncio.CancelledError:
                break

    # =========================================================================
    # Global Sniper Engine — Zero-Wake Snipe Execution
    # =========================================================================

    def register_snipe_slot(
        self,
        market_id: str,
        market: Any,
        active_orders: Dict,
        summary: Any,
    ) -> SnipeSlot:
        """Called by MakerLoop when entering FARMING state.

        Registers this market's snipe context so the GlobalSniperEngine
        can fire snipes directly without waiting for the MakerLoop to wake.
        """
        slot = SnipeSlot(
            market_id=market_id,
            market=market,
            active_orders=active_orders,
            summary=summary,
        )
        self._snipe_slots[market_id] = slot
        self.logger.debug("Snipe slot registered: %s", market_id[:16])
        return slot

    def unregister_snipe_slot(self, market_id: str) -> None:
        """Called by MakerLoop on shutdown / window end."""
        self._snipe_slots.pop(market_id, None)
        self.logger.debug("Snipe slot unregistered: %s", market_id[:16])

    async def _global_snipe_burst(
        self,
        direction: str,
        momentum: float,
        slots: List[SnipeSlot],
    ) -> None:
        """Fire snipes for ALL registered markets CONCURRENTLY.

        This runs as a task scheduled by _on_tick via loop.create_task().
        It executes in the NEXT event loop iteration (<1ms after spike
        detection), bypassing the 114ms MakerLoop wake penalty entirely.

        Results are written back to each SnipeSlot.last_result so the
        MakerLoop can pick them up and update its state.
        """
        t0 = time.time()

        async def _fire_one(slot: SnipeSlot) -> SnipeResult:
            try:
                result = await self.sniper.execute(
                    direction=direction,
                    market=slot.market,
                    active_orders=slot.active_orders,
                    summary=slot.summary,
                )
                slot.last_result = result
                slot.fired_at = t0

                if result.success:
                    slot.summary.snipes_fired += 1

                return result
            except Exception as e:
                self.logger.error(
                    "GlobalSnipe error for %s: %s",
                    slot.market_id[:16], e, exc_info=True,
                )
                return SnipeResult(
                    success=False, direction=direction,
                    price_paid=None, shares=0,
                    order_id=None, cancels_fired=0,
                    latency_ms=0, error=str(e),
                )

        # Fire ALL snipes concurrently — this is the whole point
        results = await asyncio.gather(
            *[_fire_one(slot) for slot in slots],
            return_exceptions=True,
        )

        elapsed = (time.time() - t0) * 1000
        fills = sum(
            1 for r in results
            if isinstance(r, SnipeResult) and r.success
        )
        self.logger.info(
            "🔥 GLOBAL SNIPE BURST COMPLETE | direction=%s | "
            "%d/%d markets filled | burst_latency=%.1fms",
            direction, fills, len(slots), elapsed,
        )

    # =========================================================================
    # Discovery
    # =========================================================================

    def _get_window_duration(self, market: Any) -> str:
        """Detect window duration type from market slug.

        Returns:
            '5m'  — 5-minute window
            '15m' — 15-minute window
            '1h'  — 1-hour window
        """
        slug = (market.slug or market.id or "").lower()
        if "5m" in slug:
            return "5m"
        if "15m" in slug:
            return "15m"
        return "1h"

    async def _discovery_cycle(self) -> None:
        """Scan for new windows and start sessions for any new ones.

        Slot allocation policy (80-90% toward 15-minute windows):
          - Total slots:    MAX_CONCURRENT_SESSIONS (6)
          - 1h slots cap:   MAX_1H_SESSIONS (1)
          - 15m slots:      remaining 5 slots (~83%)
        """
        gabagool_cfg = self.config.gabagool if hasattr(self.config, 'gabagool') else self.config
        assets = getattr(gabagool_cfg, 'target_assets', ['BTC', 'ETH', 'SOL'])

        try:
            markets = await asyncio.to_thread(
                self.gamma.get_all_active_markets, assets
            )
        except Exception as e:
            self.logger.warning("Market discovery failed: %s", e)
            return

        self.logger.debug("Discovered %d active markets", len(markets))

        for market in markets:
            if market.id in self._seen_market_ids:
                continue
            if market.id in self._sessions:
                continue

            active_sessions = [s for s in self._sessions.values() if not s.completed]
            active_count = len(active_sessions)

            if active_count >= MAX_CONCURRENT_SESSIONS:
                self.logger.warning(
                    "Max concurrent sessions (%d) reached, skipping %s",
                    MAX_CONCURRENT_SESSIONS, market.id[:16]
                )
                continue

            # ── 1-hour window cap enforcement ─────────────────────────────
            duration = self._get_window_duration(market)
            if duration == "1h":
                active_1h = sum(
                    1 for s in active_sessions
                    if self._get_window_duration(s.market) == "1h"
                )
                if active_1h >= MAX_1H_SESSIONS:
                    self.logger.debug(
                        "1h slot cap (%d) reached — skipping 1h window %s "
                        "to preserve 15m capacity",
                        MAX_1H_SESSIONS, market.id[:16]
                    )
                    continue
            # ──────────────────────────────────────────────────────────────

            if not market.active:
                continue
            if not market.yes_token_id or not market.no_token_id:
                self.logger.debug("Skipping %s: missing token IDs", market.id[:16])
                continue

            self._seen_market_ids.add(market.id)
            await self._start_session(market)

    async def _start_session(self, market: Market) -> None:
        """Start a MakerLoop session for a window."""
        market_id  = market.id
        window_end = self._estimate_window_end(market)

        if window_end is None:
            self.logger.warning(
                "Could not determine end time for %s, skipping", market_id[:16]
            )
            return

        seconds_remaining = (window_end - datetime.now()).total_seconds()
        # Adaptive minimum: 60s for 5m windows, 180s for longer
        slug = (market.slug or market.id or "").lower()
        min_remaining = 60 if "5m" in slug else 180
        if seconds_remaining < min_remaining:
            self.logger.debug(
                "Window %s has only %.0fs remaining, skipping",
                market_id[:16], seconds_remaining
            )
            return

        self.logger.info(
            "Starting session: %s | %s | ends in %.0fs",
            market_id[:16],
            getattr(market, 'question', 'unknown'),
            seconds_remaining
        )

        # Per-market price feed (used for bid fetching in farming)
        asset      = self._get_market_asset(market)
        price_feed = self.price_feeds.feeds.get(asset or "BTC")

        if price_feed is None or not price_feed.has_data():
            self.logger.warning(
                "No price feed for %s (%s) — skipping session",
                market_id[:16], asset
            )
            return

        # Build MakerLoop
        gabagool_cfg = self.config.gabagool if hasattr(self.config, 'gabagool') else self.config

        # Adaptive buffer based on window duration
        slug = (market.slug or market.id or "").lower()
        if "5m" in slug:
            posting_buffer = 60     # 1 min buffer for 5-min windows
        elif "15m" in slug:
            posting_buffer = 180    # 3 min buffer for 15-min windows
        else:
            posting_buffer = 180    # 3 min buffer for 1-hour windows

        maker = MakerLoop(
            bot=self.bot,
            dry_run=self.dry_run,
            farm_shares=getattr(gabagool_cfg, 'farm_shares', 10),
            farm_max_shares=getattr(gabagool_cfg, 'farm_max_shares', 100),
            stop_posting_buffer_s=posting_buffer,
        )

        task = asyncio.create_task(
            self._run_session_with_delay(maker, market, window_end, price_feed),
            name=f"session_{market_id[:16]}"
        )

        session = WindowSession(market=market, task=task)
        self._sessions[market_id] = session

        self.logger.info(
            "Session started: %s (total active: %d)",
            market_id[:16], len(self._sessions)
        )

    async def _run_session_with_delay(
        self,
        maker: MakerLoop,
        market: Market,
        window_end: datetime,
        price_feed: Any,
    ) -> None:
        """Wait for start delay then run the maker loop."""
        market_id = market.id

        self.logger.debug(
            "Waiting %ds before starting session for %s",
            WINDOW_START_DELAY_S, market_id[:16]
        )
        await asyncio.sleep(WINDOW_START_DELAY_S)

        try:
            summary = await maker.run(
                market=market,
                window_end=window_end,
                spike_detector=self.spike_detector,   # kept for cooldown state checks
                price_feed=price_feed,
                sniper=self.sniper,
                burst_signal=self._burst,              # shared burst broadcast
                risk_manager=self.risk_manager,
                stats_tracker=self.stats_tracker,
                db=self.db,
                window_manager=self,                   # GlobalSniperEngine slot registration
            )

            if market_id in self._sessions:
                self._sessions[market_id].summary   = summary
                self._sessions[market_id].completed = True

            self.completed_windows.append(summary)

            self.logger.info(
                "Session completed: %s | lean=%s | invested=$%.2f | snipes=%d",
                market_id[:16],
                summary.lean_direction or "even",
                summary.total_invested,
                summary.snipes_fired,
            )

            if self.paper_trader:
                self.paper_trader.queue_for_settlement(summary)

        except Exception as e:
            self.logger.error("Session error for %s: %s", market_id[:16], e, exc_info=True)
            if market_id in self._sessions:
                self._sessions[market_id].completed = True

    # =========================================================================
    # Cleanup
    # =========================================================================

    async def _cleanup_completed_sessions(self) -> None:
        completed_ids = [
            mid for mid, s in self._sessions.items() if s.completed
        ]
        for market_id in completed_ids:
            session = self._sessions.pop(market_id)
            if not session.task.done():
                session.task.cancel()
        if completed_ids:
            self.logger.debug("Cleaned up %d completed sessions", len(completed_ids))

    async def _shutdown_all_sessions(self) -> None:
        self.logger.info("Shutting down %d active sessions...", len(self._sessions))
        for session in self._sessions.values():
            if not session.task.done():
                session.task.cancel()
        if self._sessions:
            await asyncio.gather(*[s.task for s in self._sessions.values()], return_exceptions=True)
        self._sessions.clear()

    # =========================================================================
    # Helpers
    # =========================================================================

    def _estimate_window_end(self, market: Market) -> Optional[datetime]:
        if market.end_date:
            end = market.end_date
            if end.tzinfo is not None:
                end = end.replace(tzinfo=None)
            return end
        try:
            slug   = market.slug or market.id
            ts_str = slug.rsplit("-", 1)[-1]
            ts     = int(ts_str)
            # Detect duration from slug pattern
            if "5m" in slug:
                return datetime.fromtimestamp(ts) + timedelta(minutes=5)
            elif "15m" in slug:
                return datetime.fromtimestamp(ts) + timedelta(minutes=15)
            else:
                return datetime.fromtimestamp(ts) + timedelta(minutes=60)
        except (ValueError, IndexError):
            pass
        return datetime.now() + timedelta(minutes=10)

    def _get_market_asset(self, market: Market) -> Optional[str]:
        slug = (market.slug or market.id or "").lower()
        if "btc" in slug:
            return "BTC"
        if "eth" in slug:
            return "ETH"
        if "sol" in slug:
            return "SOL"
        return None

    def _log_status(self) -> None:
        active = [s for s in self._sessions.values() if not s.completed]
        if active:
            self.logger.info(
                "Active sessions: %d | Completed total: %d",
                len(active), len(self.completed_windows)
            )

    def stop(self) -> None:
        self.running = False
