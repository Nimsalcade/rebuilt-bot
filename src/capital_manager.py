#!/usr/bin/env python3
"""
Gabagool Bot - Capital Manager (Continuous Flow)

PURPOSE
-------
Enforces the global stop-loss based on *realized PnL*, not instantaneous cash balance.
A market-neutral bot will have wildly swinging cash balances due to temporary 
live inventory. We only measure true loss:

Realized PnL = (Merged Returns + Redeemed Returns) - Gross Spent on Resolved Markets

Stop-Loss triggers if Realized PnL falls below a fixed floor relative to the 
initial deposit (e.g. -15% of $1000 = -$150).
"""

import asyncio
import logging
from typing import Optional, Any

import src.terminal_ui as terminal_ui

# Fixed stop-loss floor relative to initial deposit
STOP_LOSS_TOLERANCE_PCT = 0.15


class CapitalManager:
    """Tracks realized PnL and enforces global stop-loss."""

    def __init__(
        self,
        bot,
        session_capital_usd: float = 1000.0,
        auto_compound_pct: float = 0.0,
        shutdown_event: Optional[asyncio.Event] = None,
        logger: Optional[logging.Logger] = None,
        paper_trader: Optional[Any] = None,
    ):
        self.bot = bot
        self.paper_trader = paper_trader
        self.starting_deposit = session_capital_usd
        self.auto_compound_pct = max(0.0, min(1.0, auto_compound_pct))
        self.shutdown_event = shutdown_event
        self.logger = logger or logging.getLogger("capital_manager")

        self.total_merged_returns = 0.0
        self.gross_spent_resolved = 0.0
        self.windows_resolved = 0
        
        # Initialize pending merge proceeds on the bot so MergeEngine can update it
        if not hasattr(self.bot, 'pending_merge_proceeds'):
            self.bot.pending_merge_proceeds = 0.0

        self.logger.info(
            "CapitalManager ready | initial_deposit=$%.2f | auto_compound=%.0f%%",
            self.starting_deposit, self.auto_compound_pct * 100
        )

    async def get_available_balance(self) -> float:
        """Fetch live usable balance from bot/cache."""
        if hasattr(self.bot, 'config') and getattr(self.bot.config, 'dry_run', False):
            if self.paper_trader and hasattr(self.paper_trader, 'ledger'):
                return self.paper_trader.ledger.get("current_balance", self.starting_deposit)
            return self.starting_deposit

        try:
            # We fetch the cached balance directly, plus any un-settled merge proceeds
            raw = getattr(self.bot, '_cached_balance_micro', self.starting_deposit * 1_000_000)
            pending = getattr(self.bot, 'pending_merge_proceeds', 0.0)
            return (raw / 1_000_000) + pending
        except Exception as exc:
            self.logger.warning("Balance read failed: %s", exc)
            return self.starting_deposit

    def record_window_resolution(self, gross_spent: float, merged_returned: float) -> None:
        """Called when a market window closes (moves to resolution)."""
        self.gross_spent_resolved += gross_spent
        self.total_merged_returns += merged_returned
        self.windows_resolved += 1

    def record_redemption(self, redeemed_amount: float, naked_cost_basis: float) -> None:
        """Called when auto_redeem successfully claims a winning ticket."""
        self.total_redeemed_returns += redeemed_amount
        self.gross_spent_resolved += naked_cost_basis

    def check_stop_loss(self) -> bool:
        """
        Evaluate if realized PnL hit the fixed floor.
        Returns False if stop-loss triggered, True otherwise.
        """
        realized_pnl = (self.total_merged_returns + self.total_redeemed_returns) - self.gross_spent_resolved
        floor = - (self.starting_deposit * STOP_LOSS_TOLERANCE_PCT)

        if self.windows_resolved > 0 and self.windows_resolved % 5 == 0:
            self.logger.info(
                "📈 PnL Check | Windows: %d | Spent: $%.2f | Merged: $%.2f | Redeemed: $%.2f | Realized Net: $%.2f",
                self.windows_resolved, self.gross_spent_resolved, self.total_merged_returns, self.total_redeemed_returns, realized_pnl
            )

        if realized_pnl <= floor:
            self.logger.critical("=" * 60)
            self.logger.critical(
                "🛑 STOP-LOSS TRIGGERED — Realized PnL $%.2f hit floor $%.2f",
                realized_pnl, floor
            )
            self.logger.critical("=" * 60)
            if self.shutdown_event is not None:
                self.shutdown_event.set()
            return False

        return True
