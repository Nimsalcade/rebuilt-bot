#!/usr/bin/env python3
"""
Gabagool Bot - Window Manager (Multi-Window Lifecycle Orchestration)

Purpose:
    Discovers active 15-minute windows for BTC/ETH/SOL and manages
    concurrent MakerLoop sessions. Implements continuous overlapping flow,
    dynamically allocating available capital across concurrent windows
    without waiting for settlement.

Author: AI-Generated
Created: 2026-05-30
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
DISCOVERY_INTERVAL_S = 15

# Max concurrent 1h sessions to avoid concentrating risk
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
    Discovers and manages continuous overlapping 15-minute window sessions.

    Lifecycle per window:
    1. Gamma API signals window is open and accepting orders
    2. Starts MakerLoop (FARMING) as a task
    3. MakerLoop runs until window closes (~14.5 min mark)
    4. Session is cleaned up, gross spent and merged returns recorded to CapitalManager
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

        # ── Capital Manager (Continuous) ───────────────────────────────────────
        gabagool_cfg = getattr(config, 'gabagool', config)
        session_capital = getattr(gabagool_cfg, 'session_capital_usd', 1000.0)
        auto_compound = getattr(gabagool_cfg, 'auto_compound_pct', 0.0)
        
        self.capital_mgr = CapitalManager(
            bot=bot,
            session_capital_usd=session_capital,
            auto_compound_pct=auto_compound,
            logger=logging.getLogger("capital_manager"),
            paper_trader=self.paper_trader,
        )
        self.window_capital_cap = 0.0

    # =========================================================================
    # Main Loop (Continuous Flow)
    # =========================================================================

    async def run_forever(self) -> None:
        self.running = True
        gabagool_cfg  = self.config.gabagool if hasattr(self.config, 'gabagool') else self.config
        target_assets = getattr(gabagool_cfg, 'target_assets', ['BTC', 'ETH', 'SOL'])
        
        # Concurrency limit pulls from config
        max_concurrent = max(1, getattr(gabagool_cfg, 'max_concurrent_arbitrages', 2))

        self.logger.info(
            "WindowManager starting | assets=%s | max_concurrent=%d | dry_run=%s",
            target_assets, max_concurrent, self.dry_run
        )

        try:
            while self.running:
                # 1. Update live balance and check global Stop-Loss
                available_balance = await self.capital_mgr.get_available_balance()
                ok = self.capital_mgr.check_stop_loss()
                if not ok:
                    self.logger.warning("CapitalManager triggered Stop-Loss — stopping WindowManager")
                    break

                # 2. Update soft capital cap per window (distribute available pool)
                self.window_capital_cap = available_balance / max_concurrent

                # 3. Clear dead sessions and record their resolved PnL
                await self._cleanup_sessions()

                # 4. Discover new windows if we have room
                if len(self._sessions) < max_concurrent:
                    await self._discover_windows(target_assets, max_concurrent)

                await asyncio.sleep(DISCOVERY_INTERVAL_S)

        except asyncio.CancelledError:
            self.logger.info("WindowManager cancelled")
        except Exception as e:
            self.logger.error("WindowManager error: %s", e, exc_info=True)
        finally:
            await self._shutdown()

    async def _discover_windows(self, assets: List[str], max_concurrent: int) -> None:
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
            if len(self._sessions) >= max_concurrent:
                break
                
            duration = (m.slug or m.id or "").lower()
            if "15m" not in duration and ("up-or-down" in duration or "1h" in duration):
                active_1h = sum(
                    1 for s in self._sessions.values()
                    if "15m" not in (s.market.slug or s.market.id or "").lower()
                )
                if active_1h >= MAX_1H_SESSIONS:
                    continue

            self._seen_market_ids.add(m.id)
            self._start_session(m)

    def _start_session(self, market: Market) -> None:
        """Spawn a MakerLoop task for the market."""
        self.logger.info(
            "Starting MakerLoop for %s | ends: %s",
            market.id[:16], market.end_date.strftime("%H:%M:%S") if market.end_date else "N/A"
        )

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
        """Remove completed sessions and pass their final PnL stats to CapitalManager."""
        done_ids = []
        for mid, session in self._sessions.items():
            if session.task.done():
                done_ids.append(mid)
                try:
                    summary = session.task.result()
                    session.summary = summary
                    if summary:
                        # Report to CapitalManager for Realized PnL tracking
                        self.capital_mgr.record_window_resolution(
                            gross_spent=summary.total_invested,
                            merged_returned=summary.merged_usdc
                        )
                        if self.paper_trader:
                            self.paper_trader.queue_for_settlement(summary)
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
