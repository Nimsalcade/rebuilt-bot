#!/usr/bin/env python3
"""
Gabagool Bot - Window Manager (Multi-Window Lifecycle Orchestration)

Purpose:
    Discovers active 15-minute windows for BTC/ETH/SOL and manages
    concurrent MakerLoop sessions. Handles window open detection,
    concurrent session limits, and graceful shutdown for pure spread arbitrage.

Author: AI-Generated
Created: 2026-05-29
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, Optional, Any, List

from .maker_loop import MakerLoop, WindowFillSummary
from .gamma_client import GammaClient, Market
from .capital_manager import CapitalManager


# How often to scan for new windows (seconds)
DISCOVERY_INTERVAL_S = 30

# How many seconds after a window opens to start the maker loop
WINDOW_START_DELAY_S = 30

# Max concurrent active sessions across all assets
MAX_CONCURRENT_SESSIONS = 6
MAX_1H_SESSIONS = 1


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
    3. Starts MakerLoop (FARMING) as a task
    4. MakerLoop runs until window closes (~14.5 min mark)
    5. Session is cleaned up, results queued for paper settlement
    """

    def __init__(
        self,
        bot: Any,
        config: Any,
        db: Any = None,
        risk_manager: Any = None,
        stats_tracker: Any = None,
        dry_run: bool = False,
        paper_trader: Any = None,
    ):
        self.bot             = bot
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

        # ── Capital Manager ───────────────────────────────────────────────────
        gabagool_cfg = getattr(config, 'gabagool', config)
        session_capital = getattr(gabagool_cfg, 'session_capital_usd', 200.0)
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
        self.running = True
        gabagool_cfg  = self.config.gabagool if hasattr(self.config, 'gabagool') else self.config
        target_assets = getattr(gabagool_cfg, 'target_assets', ['BTC', 'ETH', 'SOL'])

        self.logger.info(
            "WindowManager starting | assets=%s | dry_run=%s",
            target_assets, self.dry_run
        )

        try:
            while self.running:
                # 1. Capital Manager Phase — wait for settlement, enforce cycle limits
                ok = await self._capital_mgr.start_cycle()
                if not ok:
                    self.logger.warning("CapitalManager blocked cycle start — stopping")
                    break

                # Update MakerLoop per-window capital limit
                concurrent = max(1, getattr(gabagool_cfg, 'max_concurrent_arbitrages', 4))
                self.window_capital_cap = self._capital_mgr.session_capital_usd / concurrent

                self.logger.info("Cycle started. Watching for windows...")

                cycle_active = True
                while cycle_active and self.running:
                    # Clear dead sessions
                    await self._cleanup_sessions()

                    # Find new windows
                    if len(self._sessions) < MAX_CONCURRENT_SESSIONS:
                        await self._discover_windows(target_assets)

                    # Cycle ends when we have NO active sessions, BUT we must
                    # ensure we actually traded something this cycle before advancing.
                    # Simplified: if we have zero sessions, and we've already discovered some,
                    # we can end the cycle. To prevent rapid-fire empty cycles, we'll
                    # only break if we have no active sessions AND we've completed at least one.
                    
                    if not self._sessions and self.completed_windows:
                        self.logger.info("All windows in cycle finished. Advancing to next cycle.")
                        self.completed_windows.clear()
                        cycle_active = False

                    await asyncio.sleep(DISCOVERY_INTERVAL_S)

                # End cycle — records P&L, sets up for settlement wait on next loop
                await self._capital_mgr.end_cycle()

        except asyncio.CancelledError:
            self.logger.info("WindowManager cancelled")
        except Exception as e:
            self.logger.error("WindowManager error: %s", e, exc_info=True)
        finally:
            await self._shutdown()

    async def _discover_windows(self, assets: List[str]) -> None:
        """Query Gamma API for active windows on target assets."""
        try:
            markets = await asyncio.to_thread(self.gamma.get_all_active_markets, assets)
        except Exception as e:
            self.logger.warning("Gamma API error during discovery: %s", e)
            return

        now = datetime.now(timezone.utc)
        for m in markets:
            if m.id in self._seen_market_ids:
                continue

            # Capacity checks
            if len(self._sessions) >= MAX_CONCURRENT_SESSIONS:
                break
                
            is_1h = "15m" not in m.slug.lower()
            if is_1h:
                current_1h = sum(1 for s in self._sessions.values() if "15m" not in s.market.slug.lower())
                if current_1h >= MAX_1H_SESSIONS:
                    continue



            self._seen_market_ids.add(m.id)
            self._start_session(m)

    def _start_session(self, market: Market) -> None:
        """Spawn a MakerLoop task for the market."""
        self.logger.info(
            "Starting MakerLoop for %s (%s) | ends: %s",
            market.id[:16], market.asset, market.end_date.strftime("%H:%M:%S")
        )

        gabagool_cfg = getattr(self.config, 'gabagool', self.config)

        # Simplified initialization without sniper/spike_detector
        loop = MakerLoop(
            bot=self.bot,
            dry_run=self.dry_run,
            farm_shares=10,
            farm_max_shares=500,
            stop_posting_buffer_s=60,
            window_capital_cap=self.window_capital_cap,
        )

        task = asyncio.create_task(
            loop.run(
                market=market,
                window_end=market.end_date.replace(tzinfo=None), # drop tz for simplicity
                risk_manager=self.risk_manager,
                stats_tracker=self.stats_tracker,
                db=self.db,
            ),
            name=f"maker_loop_{market.id[:8]}"
        )

        self._sessions[market.id] = WindowSession(market, task)

    async def _cleanup_sessions(self) -> None:
        """Remove completed sessions and process their summaries."""
        done_ids = []
        for mid, session in self._sessions.items():
            if session.task.done():
                done_ids.append(mid)
                try:
                    summary = session.task.result()
                    session.summary = summary
                    if summary:
                        self.completed_windows.append(summary)
                        if self.paper_trader:
                            self.paper_trader.record_window_close(summary)
                except asyncio.CancelledError:
                    self.logger.info("Session %s was cancelled", mid[:16])
                except Exception as e:
                    self.logger.error("Session %s failed: %s", mid[:16], e, exc_info=True)

        for mid in done_ids:
            del self._sessions[mid]

    async def _shutdown(self) -> None:
        """Gracefully terminate all running MakerLoop sessions."""
        self.logger.info("Shutting down WindowManager... cancelling %d sessions", len(self._sessions))
        for mid, session in self._sessions.items():
            if not session.task.done():
                session.task.cancel()
        
        # Wait for them to finish cancelling
        if self._sessions:
            await asyncio.gather(
                *(s.task for s in self._sessions.values()),
                return_exceptions=True
            )
        self._sessions.clear()
        self.logger.info("WindowManager shutdown complete")
