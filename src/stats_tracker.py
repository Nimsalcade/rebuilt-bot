#!/usr/bin/env python3
"""
Gabagool Bot - Statistics Tracker

Purpose:
    Track bot performance metrics over time.
    Records trades, calculates win rates, and provides summaries.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Based on: samples/warproxxx-maker/update_stats.py
    Also reference: samples/warproxxx-maker/poly_stats/account_stats.py
    Key patterns to extract:
        - Trade recording with timestamps
        - Performance metric calculations
        - JSON persistence

Dependencies:
    - json
    - dataclasses
    - datetime

Usage:
    from src.stats_tracker import StatsTracker

    tracker = StatsTracker()
    tracker.record_trade(market_id, yes_price, no_price, profit_margin)
    tracker.update_trade_result(market_id, 'success', actual_profit)
    tracker.print_summary()

Notes:
    - Persists to JSON file for recovery
    - Calculates win rate, avg profit, total profit
    - Print summary shows key metrics
"""

import json
import logging
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional
from pathlib import Path


@dataclass
class TradeRecord:
    """
    Record of a single trade attempt.

    Attributes:
        timestamp: When trade was executed
        market_id: Market identifier
        yes_price: YES price at execution
        no_price: NO price at execution
        combined_cost: Total cost (YES + NO)
        profit_margin: Expected profit margin
        trade_size: USD amount per side
        result: 'pending', 'success', 'failed', 'expired'
        actual_profit: Realized profit (0 until settled)
        notes: Optional notes
    """
    timestamp: str  # ISO format
    market_id: str
    yes_price: float
    no_price: float
    combined_cost: float
    profit_margin: float
    trade_size: float = 0.0
    result: str = "pending"
    actual_profit: float = 0.0
    notes: str = ""


class StatsTracker:
    """
    Performance statistics tracker.

    Based on warproxxx/poly-maker update_stats.py pattern.
    Tracks all trades and calculates performance metrics.
    """

    def __init__(self, stats_file: str = "performance_stats.json"):
        """
        Initialize stats tracker.

        Args:
            stats_file: Path to JSON file for persistence
        """
        self.stats_file = Path(stats_file)
        self.trades: List[TradeRecord] = []
        self.logger = logging.getLogger("stats_tracker")
        self.load_stats()

    def record_trade(
        self,
        market_id: str,
        yes_price: float,
        no_price: float,
        profit_margin: float,
        trade_size: float = 0.0,
        notes: str = ""
    ) -> None:
        """
        Record a new arbitrage trade attempt.

        Args:
            market_id: Market identifier
            yes_price: YES price at execution
            no_price: NO price at execution
            profit_margin: Expected profit margin
            trade_size: USD per side
            notes: Optional notes
        """
        trade = TradeRecord(
            timestamp=datetime.now().isoformat(),
            market_id=market_id,
            yes_price=yes_price,
            no_price=no_price,
            combined_cost=yes_price + no_price,
            profit_margin=profit_margin,
            trade_size=trade_size,
            result="pending",
            notes=notes
        )
        self.trades.append(trade)
        self.save_stats()

        self.logger.info(
            "Trade recorded: %s | Margin: %.2f%% | Size: $%.2f",
            market_id[:16], profit_margin * 100, trade_size * 2
        )

    def update_trade_result(
        self,
        market_id: str,
        result: str,
        actual_profit: float,
        notes: str = ""
    ) -> bool:
        """
        Update trade result when settled.

        Args:
            market_id: Market identifier
            result: 'success', 'failed', 'expired'
            actual_profit: Realized profit in USD
            notes: Optional notes

        Returns:
            True if trade found and updated
        """
        # Find most recent pending trade for this market
        for trade in reversed(self.trades):
            if trade.market_id == market_id and trade.result == "pending":
                trade.result = result
                trade.actual_profit = actual_profit
                if notes:
                    trade.notes = notes
                self.save_stats()

                self.logger.info(
                    "Trade settled: %s | Result: %s | Profit: $%.4f",
                    market_id[:16], result, actual_profit
                )
                return True

        self.logger.warning("No pending trade found for %s", market_id[:16])
        return False

    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Calculate overall performance metrics.

        Returns:
            Dict with performance statistics
        """
        if not self.trades:
            return {
                "total_trades": 0,
                "completed_trades": 0,
                "successful_trades": 0,
                "failed_trades": 0,
                "expired_trades": 0,
                "pending_trades": 0,
                "win_rate": 0.0,
                "avg_profit": 0.0,
                "total_profit": 0.0,
                "avg_margin": 0.0,
                "total_volume": 0.0,
                "last_updated": datetime.now().isoformat()
            }

        completed = [t for t in self.trades if t.result != "pending"]
        successful = [t for t in completed if t.result == "success"]
        failed = [t for t in completed if t.result == "failed"]
        expired = [t for t in completed if t.result == "expired"]
        pending = [t for t in self.trades if t.result == "pending"]

        win_rate = len(successful) / len(completed) if completed else 0.0
        avg_profit = (
            sum(t.actual_profit for t in successful) / len(successful)
            if successful else 0.0
        )
        total_profit = sum(t.actual_profit for t in successful)
        avg_margin = (
            sum(t.profit_margin for t in self.trades) / len(self.trades)
            if self.trades else 0.0
        )
        total_volume = sum(t.trade_size * 2 for t in self.trades)

        return {
            "total_trades": len(self.trades),
            "completed_trades": len(completed),
            "successful_trades": len(successful),
            "failed_trades": len(failed),
            "expired_trades": len(expired),
            "pending_trades": len(pending),
            "win_rate": win_rate,
            "avg_profit": avg_profit,
            "total_profit": total_profit,
            "avg_margin": avg_margin,
            "total_volume": total_volume,
            "last_updated": datetime.now().isoformat()
        }

    def get_recent_trades(self, limit: int = 10) -> List[TradeRecord]:
        """Get most recent trades."""
        return self.trades[-limit:] if self.trades else []

    def get_trades_by_result(self, result: str) -> List[TradeRecord]:
        """Get trades filtered by result."""
        return [t for t in self.trades if t.result == result]

    def save_stats(self) -> None:
        """Persist stats to JSON file."""
        try:
            data = {
                "trades": [asdict(t) for t in self.trades],
                "summary": self.get_performance_summary()
            }
            with open(self.stats_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            self.logger.error("Failed to save stats: %s", e)

    def load_stats(self) -> None:
        """Load stats from JSON file."""
        try:
            if self.stats_file.exists():
                with open(self.stats_file, "r") as f:
                    data = json.load(f)
                    self.trades = [
                        TradeRecord(**t) for t in data.get("trades", [])
                    ]
                self.logger.info("Loaded %d historical trades", len(self.trades))
        except Exception as e:
            self.logger.warning("Could not load stats: %s", e)
            self.trades = []

    def print_summary(self) -> None:
        """Print performance summary to console."""
        summary = self.get_performance_summary()

        print("\n" + "=" * 50)
        print("GABAGOOL BOT PERFORMANCE SUMMARY")
        print("=" * 50)
        print(f"Total Trades:     {summary['total_trades']}")
        print(f"Completed:        {summary['completed_trades']}")
        print(f"Successful:       {summary['successful_trades']}")
        print(f"Failed:           {summary['failed_trades']}")
        print(f"Expired:          {summary['expired_trades']}")
        print(f"Pending:          {summary['pending_trades']}")
        print("-" * 50)
        print(f"Win Rate:         {summary['win_rate']:.1%}")
        print(f"Avg Profit:       ${summary['avg_profit']:.4f}")
        print(f"Total Profit:     ${summary['total_profit']:.2f}")
        print(f"Avg Margin:       {summary['avg_margin']:.2%}")
        print(f"Total Volume:     ${summary['total_volume']:.2f}")
        print("=" * 50 + "\n")

    def reset_stats(self) -> None:
        """Reset all statistics (use with caution)."""
        self.trades = []
        self.save_stats()
        self.logger.warning("Stats have been reset")
