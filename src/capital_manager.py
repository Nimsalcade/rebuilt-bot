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
from dataclasses import dataclass
from typing import Optional, Any, Dict

import src.terminal_ui as terminal_ui

# Fixed stop-loss floor relative to initial deposit
STOP_LOSS_TOLERANCE_PCT = 0.15


@dataclass
class NakedPosition:
    """An open block of unmatched (naked) leftover shares awaiting on-chain
    resolution.

    The merged portion of a window is booked at window close. The naked
    portion must NOT be booked then — its cost basis is only realized when the
    underlying market resolves on-chain, for BOTH outcomes:

        winner -> gross += naked_cost_basis, redeemed += payout
        loser  -> gross += naked_cost_basis, redeemed += 0

    Tracking these per market is what lets the stop-loss see losing legs.
    Without it, only winning legs (the only ones the Data API ever returns as
    "redeemable") get booked and realized PnL is permanently overstated.
    """
    condition_id:     str
    naked_side:       Optional[str]   # "UP" / "DOWN" / None (which leg is naked)
    naked_shares:     float           # leftover share count (informational)
    naked_cost_basis: float           # USD actually paid for the naked leg
    resolved:         bool = False    # True once booked into realized PnL


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
        # Sum of $1.00 payouts claimed on winning naked legs at resolution.
        # (Re-added — a prior edit deleted this, which made check_stop_loss and
        # the redemption path raise AttributeError on first use.)
        self.total_redeemed_returns = 0.0
        self.gross_spent_resolved = 0.0
        self.windows_resolved = 0

        # Registry of open naked positions awaiting on-chain resolution, keyed
        # by condition_id. Populated at window close (register_naked_position),
        # drained at resolution (record_naked_resolution) for winners and losers
        # alike. This is the conditionId -> naked_cost_basis lookup the redeemer
        # reads so it can book cost basis it would otherwise never see.
        self._naked_positions: Dict[str, NakedPosition] = {}

        # Initialize pending merge proceeds on the bot so MergeEngine can update
        # it even if the bot was constructed without it (bot.py owns the reset).
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
            # Cached on-chain balance plus any merge proceeds not yet reflected
            # in it. The bot resets `pending_merge_proceeds` to 0 on every real
            # refresh, so this sum never double-counts a settled merge.
            raw = getattr(self.bot, '_cached_balance_micro', None)
            pending = getattr(self.bot, 'pending_merge_proceeds', 0.0) or 0.0
            if raw is None:
                # No authoritative balance read yet — fall back to the deposit
                # base rather than adding pending to an unknown figure.
                return self.starting_deposit
            return (raw / 1_000_000) + pending
        except Exception as exc:
            self.logger.warning("Balance read failed: %s", exc)
            return self.starting_deposit

    def record_window_resolution(self, merged_cost_basis: float, merged_returns: float) -> None:
        """Book the MERGED portion of a window at close.

        Only the merged cost basis and its matching merged returns are booked
        here — never the naked leftover. Merged pairs are a guaranteed small
        positive (bought a set for <$1.00, merged it for $1.00), so this never
        dips. Booking the full gross here instead would show a phantom loss
        equal to the naked cost until the (later) redemption clears, which can
        false-trip the stop-loss on inventory that is about to pay out.
        """
        self.gross_spent_resolved += merged_cost_basis
        self.total_merged_returns += merged_returns
        self.windows_resolved += 1

    def register_naked_position(
        self,
        condition_id: str,
        naked_side: Optional[str],
        naked_shares: float,
        naked_cost_basis: float,
    ) -> None:
        """Register a window's leftover naked shares for resolution-time booking.

        Called at window close. The cost basis is held here (NOT yet booked into
        realized PnL) until the market resolves on-chain. If the same market is
        registered twice before it resolves, the amounts accumulate.
        """
        if not condition_id or naked_cost_basis <= 0 or naked_shares <= 0:
            return
        existing = self._naked_positions.get(condition_id)
        if existing is not None and not existing.resolved:
            existing.naked_shares += naked_shares
            existing.naked_cost_basis += naked_cost_basis
            if existing.naked_side is None:
                existing.naked_side = naked_side
        else:
            self._naked_positions[condition_id] = NakedPosition(
                condition_id=condition_id,
                naked_side=naked_side,
                naked_shares=naked_shares,
                naked_cost_basis=naked_cost_basis,
            )

    def get_naked_cost_basis(self, condition_id: str) -> float:
        """Look up the unbooked naked cost basis for a market (0.0 if none)."""
        pos = self._naked_positions.get(condition_id)
        if pos is None or pos.resolved:
            return 0.0
        return pos.naked_cost_basis

    def pending_naked_positions(self) -> list:
        """Naked positions still awaiting resolution (for the resolver sweep)."""
        return [p for p in self._naked_positions.values() if not p.resolved]

    def record_naked_resolution(
        self,
        condition_id: str,
        won: bool,
        redeemed_payout: float = 0.0,
    ) -> bool:
        """Book a naked leg's cost basis at on-chain resolution.

        Winner -> gross += naked_cost_basis, redeemed += redeemed_payout.
        Loser  -> gross += naked_cost_basis, redeemed += 0.

        Idempotent: each condition is booked exactly once. Returns True if this
        call booked the position, False if it was unknown or already booked.
        Booking the loser branch is what keeps realized PnL honest — without it
        the stop-loss is blind to the ~17% of naked legs that lose.
        """
        pos = self._naked_positions.get(condition_id)
        if pos is None or pos.resolved:
            return False

        self.gross_spent_resolved += pos.naked_cost_basis
        if won:
            self.total_redeemed_returns += max(0.0, redeemed_payout)
        pos.resolved = True

        self.logger.info(
            "Naked leg resolved | %s | %s | cost=$%.2f payout=$%.2f",
            condition_id[:16], "WON" if won else "LOST",
            pos.naked_cost_basis, redeemed_payout if won else 0.0,
        )
        return True

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
