#!/usr/bin/env python3
"""
SpreadFarmerStrategy — pure market-neutral spread arbitrage.

Wires together WindowManager (spread-only mode) and PaperTrader.
No price feeds. No spike detectors. No snipers.
"""

import asyncio
from src.window_manager import WindowManager
from src.paper_trader import PaperTrader

class SpreadFarmerStrategy:
    def __init__(self, bot, config, dry_run=False):
        self.bot = bot
        self.config = config
        self.dry_run = dry_run
        
        paper_trader = None
        if dry_run:
            paper_trader = PaperTrader(
                initial_balance=getattr(config.gabagool, "session_capital_usd", 200.0)
            )

        self.window_manager = WindowManager(
            bot=bot,
            config=config,
            dry_run=dry_run,
            paper_trader=paper_trader,
        )

    async def run(self):
        await self.window_manager.run_forever()
