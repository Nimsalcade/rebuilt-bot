#!/usr/bin/env python3
"""
Gabagool Bot - Precision Snipe Maker Strategy

Purpose:
    Top-level strategy orchestrator. Wires together:
    - PriceFeedManager (Coinbase WebSocket)
    - SpikeDetector (5s latency arbitrage signal)
    - Sniper (cancel-and-snipe execution)
    - MakerLoop (per-window farming + snipe state machine)
    - WindowManager (15-minute session lifecycle)
    - PaperTrader (simulated settlement tracking)

This replaces momentum_maker.py with the real Gabagool22 strategy.

Author: AI-Generated
Created: 2026-05-03
"""

import asyncio
import logging
from typing import List, Optional, Any

from src.price_feed import PriceFeedManager
from src.spike_detector import SpikeDetector
from src.sniper import Sniper
from src.window_manager import WindowManager
from src.paper_trader import PaperTrader


logger = logging.getLogger("snipe_maker")


def _get_cfg(config: Any, key: str, default):
    """
    Safely get a config value from either a Config object (attribute access)
    or a plain dict.
    """
    gabagool = getattr(config, "gabagool", None)
    if gabagool is not None:
        return getattr(gabagool, key, default)
    if isinstance(config, dict):
        return config.get("gabagool", {}).get(key, default)
    return default


def _get_data_dir(config: Any) -> str:
    if hasattr(config, "data_dir"):
        return str(config.data_dir)
    if isinstance(config, dict):
        return config.get("data_dir", "data")
    return "data"


class SnipeMakerStrategy:
    """
    Full Gabagool22 precision latency-arbitrage strategy.

    Lifecycle:
        1. Connect Coinbase WebSocket feeds (BTC, ETH, SOL)
        2. Wait for feeds to stabilize
        3. Start WindowManager — discovers live 15-min markets every 30s
        4. For each active market window, spawn a MakerLoop coroutine:
            a. FARMING:  post passive bids on both UP/DOWN
            b. SNIPING:  spike detected → cancel opposing, fire aggressive limit
            c. COOLDOWN: 45s hold, then back to FARMING
        6. PaperTrader settles completed windows vs. Polymarket resolution
    """

    def __init__(
        self,
        bot: Any,
        config: Any,                   # Config object or dict
        dry_run: bool = True,
        assets: List[str] = None,
        spike_threshold_pct: float = 0.02,
    ):
        self.bot                 = bot
        self.config              = config
        self.dry_run             = dry_run
        self.assets              = assets or ["BTC", "ETH", "SOL"]
        self.spike_threshold_pct = spike_threshold_pct

        # ── Price feeds ───────────────────────────────────────────────────────
        self.feed_manager = PriceFeedManager(assets=self.assets)

        # ── Spike detector (shared across all window sessions) ────────────────
        self.spike_detector = SpikeDetector(
            threshold_pct=spike_threshold_pct,
            window_s=5,
            cooldown_s=_get_cfg(config, "snipe_cooldown_s", 20),
        )

        # ── Sniper (stateless, shared across all windows) ─────────────────────
        self.sniper = Sniper(
            bot=bot,
            dry_run=dry_run,
            snipe_shares=10,   # $53 capital: 10 × $0.85 = $8.50 max per market
            ask_buffer=_get_cfg(config, "snipe_ask_buffer", 0.02),
            max_price=_get_cfg(config, "snipe_max_price", 0.91),
        )

        # ── Paper trader ──────────────────────────────────────────────────────
        data_dir = _get_data_dir(config)
        self.paper_trader = PaperTrader(
            ledger_path=f"{data_dir}/paper_trading.json",
            starting_balance=200.0,
        ) if dry_run else None

        # ── Window manager ────────────────────────────────────────────────────
        self.window_manager = WindowManager(
            bot=bot,
            spike_detector=self.spike_detector,
            price_feeds=self.feed_manager,
            sniper=self.sniper,
            config=config,
            dry_run=dry_run,
            paper_trader=self.paper_trader,
        )

    async def run(self) -> None:
        """Start the full strategy. Runs until cancelled."""
        logger.info("=" * 60)
        logger.info("GABAGOOL PRECISION SNIPE-MAKER STRATEGY")
        logger.info(
            "Mode: %s | Assets: %s | Spike threshold: %.3f%%",
            "DRY RUN" if self.dry_run else "LIVE 🔴",
            ", ".join(self.assets),
            self.spike_threshold_pct,
        )
        logger.info("=" * 60)

        try:
            # Step 1: Start price feeds (WebSocket connections)
            await self.feed_manager.start()
            logger.info("Waiting for price feeds to stabilize...")

            ok = await self.feed_manager.wait_for_all_feeds(timeout=20.0)
            if not ok:
                logger.warning("Some price feeds timed out — continuing with available feeds")

            # Log initial prices
            for asset in self.assets:
                price = self.feed_manager.get_price(asset)
                mom5s = self.feed_manager.get_momentum(asset, seconds=5)
                logger.info(
                    "%s price: $%.2f | 5s_momentum: %s",
                    asset, price or 0.0,
                    f"{mom5s:+.4f}%" if mom5s is not None else "N/A"
                )

            # Step 2: Start paper settlement loop (dry run only)
            tasks = []
            if self.paper_trader:
                tasks.append(asyncio.create_task(
                    self.paper_trader.run_settlement_loop(),
                    name="paper_settlement"
                ))

            # Step 3: Run window manager (this is the main blocking loop)
            tasks.append(asyncio.create_task(
                self.window_manager.run_forever(),
                name="window_manager"
            ))

            await asyncio.gather(*tasks)

        except asyncio.CancelledError:
            logger.info("Strategy cancelled — shutting down")
        except Exception as e:
            logger.error("Strategy fatal error: %s", e, exc_info=True)
        finally:
            self.feed_manager.stop_all()
            if self.paper_trader:
                self.paper_trader.stop()
            logger.info("Strategy stopped cleanly")


async def run_snipe_maker(bot: Any, config: Any, dry_run: bool = True) -> None:
    """
    Convenience entry point. Called from main.py or Makefile.

    Args:
        bot:     Authenticated TradingBot instance
        config:  Config object
        dry_run: Paper trading mode
    """
    assets          = _get_cfg(config, "target_assets", ["BTC", "ETH", "SOL"])
    spike_threshold = _get_cfg(config, "spike_threshold_pct", 0.02)

    strategy = SnipeMakerStrategy(
        bot=bot,
        config=config,
        dry_run=dry_run,
        assets=assets,
        spike_threshold_pct=spike_threshold,
    )
    await strategy.run()
