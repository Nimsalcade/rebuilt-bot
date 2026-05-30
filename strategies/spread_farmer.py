#!/usr/bin/env python3
"""
SpreadFarmerStrategy — pure market-neutral spread arbitrage.

Wires together WindowManager (Continuous Flow mode), PaperTrader,
and the AutoRedeemer background service.
"""

import asyncio
from src.window_manager import WindowManager
from src.paper_trader import PaperTrader
from src.auto_redeem import AutoRedeemer

class SpreadFarmerStrategy:
    def __init__(self, bot, config, dry_run=False):
        self.bot = bot
        self.config = config
        self.dry_run = dry_run
        
        paper_trader = None
        if dry_run:
            paper_trader = PaperTrader(
                ledger_path="data/paper_ledger.json",
                starting_balance=getattr(config.gabagool, "session_capital_usd", 1000.0)
            )

        self.window_manager = WindowManager(
            bot=bot,
            config=config,
            dry_run=dry_run,
            paper_trader=paper_trader,
        )

        # The AutoRedeemer books resolved naked legs straight into the
        # CapitalManager's realized-PnL tracker. It needs the capital manager
        # (cost-basis registry + booking) and the gamma client (on-chain
        # resolution / winning-outcome lookup for losing legs). Booking happens
        # per-position inside the redeemer where the conditionId and the real
        # claimed value are known — not via a wrapper that only sees a count.
        self.auto_redeemer = AutoRedeemer(
            wallet_address=config.safe_address,
            enabled=not dry_run,
            capital_manager=self.window_manager.capital_mgr,
            gamma_client=self.window_manager.gamma,
        )
        if not dry_run and hasattr(config, 'private_key'):
            self.auto_redeemer.initialize_web3(config.private_key)


    async def run(self):
        # Start the background auto-redeemer task
        redeem_task = asyncio.create_task(self.auto_redeemer.run_continuous())
        
        try:
            # Run the continuous flow pipeline
            await self.window_manager.run_forever()
        finally:
            self.auto_redeemer.stop()
            await redeem_task
