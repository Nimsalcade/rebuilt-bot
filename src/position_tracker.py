#!/usr/bin/env python3
"""
Gabagool Bot - Position Tracker

Purpose:
    Thread-safe tracking of YES/NO positions with time limits.
    Manages arbitrage position pairs and detects incomplete/expired positions.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Based on: samples/trust412-spike/main.py
    Key patterns to extract:
        - Thread-safe position management with locks
        - Holding time limits (30 min max)
        - Concurrent position limits

Dependencies:
    - threading
    - dataclasses
    - datetime

Usage:
    from src.position_tracker import PositionTracker, ArbitragePosition

    tracker = PositionTracker(max_concurrent=3)
    tracker.add_yes_position(market_id, shares, cost)
    tracker.add_no_position(market_id, shares, cost)

    complete = tracker.get_complete_pairs()
    expired = tracker.get_expired_positions()

Notes:
    - All methods are thread-safe (use self.lock)
    - Positions have a 30-minute time limit by default
    - Incomplete pairs are cleaned up when expired
"""

import threading
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional


@dataclass
class ArbitragePosition:
    """
    Represents an arbitrage position with YES and NO sides.

    Attributes:
        market_id: Unique market identifier
        yes_token_id: YES token contract address
        no_token_id: NO token contract address
        yes_shares: Number of YES shares held
        yes_avg_cost: Weighted average cost per YES share
        yes_total_cost: Total USD spent on YES
        no_shares: Number of NO shares held
        no_avg_cost: Weighted average cost per NO share
        no_total_cost: Total USD spent on NO
        opened_at: When position was first opened
        resolved: Whether position has been settled
        holding_time_limit: Max seconds to hold (default 30 min)
    """
    market_id: str
    yes_token_id: str = ""
    no_token_id: str = ""

    yes_shares: float = 0.0
    yes_avg_cost: float = 0.0
    yes_total_cost: float = 0.0

    no_shares: float = 0.0
    no_avg_cost: float = 0.0
    no_total_cost: float = 0.0

    opened_at: datetime = field(default_factory=datetime.now)
    resolved: bool = False
    profit: float = 0.0

    holding_time_limit: int = 1800  # 30 minutes in seconds

    @property
    def combined_avg_cost(self) -> float:
        """Average cost per outcome pair (YES + NO)."""
        total_pairs = min(self.yes_shares, self.no_shares)
        if total_pairs == 0:
            return 0.0
        return (self.yes_total_cost + self.no_total_cost) / total_pairs

    @property
    def is_complete_pair(self) -> bool:
        """Check if we have both YES and NO positions."""
        return self.yes_shares > 0 and self.no_shares > 0

    @property
    def guaranteed_profit_per_pair(self) -> float:
        """Calculate guaranteed profit per pair if arbitrage successful."""
        if not self.is_complete_pair:
            return 0.0
        return 1.0 - self.combined_avg_cost

    @property
    def total_pairs(self) -> float:
        """Number of complete pairs (min of YES and NO shares)."""
        return min(self.yes_shares, self.no_shares)

    @property
    def total_exposure(self) -> float:
        """Total USD exposure (YES cost + NO cost)."""
        return self.yes_total_cost + self.no_total_cost

    @property
    def is_expired(self) -> bool:
        """Check if position has exceeded holding time limit."""
        if not self.opened_at:
            return False
        elapsed = datetime.now() - self.opened_at
        return elapsed.total_seconds() > self.holding_time_limit

    @property
    def time_remaining(self) -> timedelta:
        """Time remaining before expiration."""
        if not self.opened_at:
            return timedelta(seconds=self.holding_time_limit)
        elapsed = datetime.now() - self.opened_at
        remaining = self.holding_time_limit - elapsed.total_seconds()
        return timedelta(seconds=max(0, remaining))


class PositionTracker:
    """
    Thread-safe position tracker for arbitrage positions.

    Based on Trust412/Polymarket-spike-bot-v1 patterns.
    Manages concurrent positions with time limits and thread safety.
    """

    def __init__(self, max_concurrent: int = 3):
        """
        Initialize position tracker.

        Args:
            max_concurrent: Maximum concurrent arbitrage positions
        """
        self.active_positions: Dict[str, ArbitragePosition] = {}
        self.max_concurrent = max_concurrent
        self.lock = threading.Lock()
        self.logger = logging.getLogger("position_tracker")

    def add_yes_position(
        self,
        market_id: str,
        shares: float,
        cost: float,
        yes_token_id: str = "",
        no_token_id: str = ""
    ) -> bool:
        """
        Add or update YES position (thread-safe).

        Args:
            market_id: Market identifier
            shares: Number of shares purchased
            cost: Total cost in USD
            yes_token_id: YES token ID (optional, for new positions)
            no_token_id: NO token ID (optional, for new positions)

        Returns:
            True if position updated successfully
        """
        with self.lock:
            if market_id not in self.active_positions:
                if not self.can_add_position():
                    self.logger.warning("Max concurrent positions reached")
                    return False

                self.active_positions[market_id] = ArbitragePosition(
                    market_id=market_id,
                    yes_token_id=yes_token_id,
                    no_token_id=no_token_id,
                    opened_at=datetime.now()
                )

            pos = self.active_positions[market_id]

            # Update weighted average cost
            total_shares = pos.yes_shares + shares
            total_cost = pos.yes_total_cost + cost

            pos.yes_shares = total_shares
            pos.yes_total_cost = total_cost
            pos.yes_avg_cost = total_cost / total_shares if total_shares > 0 else 0

            self.logger.info(
                "YES position added: %s | %.2f shares @ $%.4f",
                market_id[:16], shares, cost / shares if shares > 0 else 0
            )
            return True

    def add_no_position(
        self,
        market_id: str,
        shares: float,
        cost: float,
        yes_token_id: str = "",
        no_token_id: str = ""
    ) -> bool:
        """
        Add or update NO position (thread-safe).

        Args:
            market_id: Market identifier
            shares: Number of shares purchased
            cost: Total cost in USD
            yes_token_id: YES token ID (optional, for new positions)
            no_token_id: NO token ID (optional, for new positions)

        Returns:
            True if position updated successfully
        """
        with self.lock:
            if market_id not in self.active_positions:
                if not self.can_add_position():
                    self.logger.warning("Max concurrent positions reached")
                    return False

                self.active_positions[market_id] = ArbitragePosition(
                    market_id=market_id,
                    yes_token_id=yes_token_id,
                    no_token_id=no_token_id,
                    opened_at=datetime.now()
                )

            pos = self.active_positions[market_id]

            # Update weighted average cost
            total_shares = pos.no_shares + shares
            total_cost = pos.no_total_cost + cost

            pos.no_shares = total_shares
            pos.no_total_cost = total_cost
            pos.no_avg_cost = total_cost / total_shares if total_shares > 0 else 0

            self.logger.info(
                "NO position added: %s | %.2f shares @ $%.4f",
                market_id[:16], shares, cost / shares if shares > 0 else 0
            )
            return True

    def get_position(self, market_id: str) -> Optional[ArbitragePosition]:
        """Get position for a specific market."""
        with self.lock:
            return self.active_positions.get(market_id)

    def get_incomplete_pairs(self) -> List[ArbitragePosition]:
        """Get positions where we have one side but not the other."""
        with self.lock:
            return [
                pos for pos in self.active_positions.values()
                if not pos.is_complete_pair and not pos.resolved
            ]

    def get_complete_pairs(self) -> List[ArbitragePosition]:
        """Get positions where we have both sides (arbitrage ready)."""
        with self.lock:
            return [
                pos for pos in self.active_positions.values()
                if pos.is_complete_pair and not pos.resolved
            ]

    def get_expired_positions(self) -> List[ArbitragePosition]:
        """Get positions that exceeded time limits."""
        with self.lock:
            return [
                pos for pos in self.active_positions.values()
                if pos.is_expired and not pos.resolved
            ]

    def can_add_position(self) -> bool:
        """Check if we can add more concurrent positions."""
        # Note: Called within lock, don't acquire again
        active_count = sum(
            1 for pos in self.active_positions.values()
            if not pos.resolved
        )
        return active_count < self.max_concurrent

    def mark_resolved(self, market_id: str, profit: float = 0.0) -> bool:
        """Mark a position as resolved."""
        with self.lock:
            if market_id in self.active_positions:
                self.active_positions[market_id].resolved = True
                self.active_positions[market_id].profit = profit
                self.logger.info(
                    "Position resolved: %s | Profit: $%.4f",
                    market_id[:16], profit
                )
                return True
            return False

    def cleanup_expired(self) -> int:
        """
        Remove expired incomplete positions (risk management).

        Returns:
            Number of positions cleaned up
        """
        expired = self.get_expired_positions()
        cleaned = 0

        for pos in expired:
            if not pos.is_complete_pair:
                self.logger.warning(
                    "Cleaning up expired incomplete position: %s",
                    pos.market_id[:16]
                )
                with self.lock:
                    if pos.market_id in self.active_positions:
                        del self.active_positions[pos.market_id]
                        cleaned += 1

        return cleaned

    def get_total_exposure(self) -> float:
        """Get total USD exposure across all positions."""
        with self.lock:
            return sum(
                pos.total_exposure
                for pos in self.active_positions.values()
                if not pos.resolved
            )

    def get_summary(self) -> Dict:
        """Get summary of current positions."""
        with self.lock:
            active = [p for p in self.active_positions.values() if not p.resolved]
            complete = [p for p in active if p.is_complete_pair]
            incomplete = [p for p in active if not p.is_complete_pair]

            return {
                "total_positions": len(active),
                "complete_pairs": len(complete),
                "incomplete_pairs": len(incomplete),
                "total_exposure": sum(p.total_exposure for p in active),
                "total_potential_profit": sum(
                    p.guaranteed_profit_per_pair * p.total_pairs
                    for p in complete
                ),
            }
