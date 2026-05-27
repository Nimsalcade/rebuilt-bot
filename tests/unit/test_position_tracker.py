#!/usr/bin/env python3
"""
Unit Tests - Position Tracker

Purpose:
    Test position tracking functionality including thread-safety,
    time limits, and pair completion detection.

Author: AI-Generated
Created: 2026-01-26
"""

import unittest
import threading
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.position_tracker import PositionTracker, ArbitragePosition


class TestArbitragePosition(unittest.TestCase):
    """Tests for ArbitragePosition dataclass."""

    def test_combined_avg_cost(self):
        """Test combined average cost calculation."""
        pos = ArbitragePosition(
            market_id="test_market",
            yes_shares=10,
            yes_total_cost=4.5,  # $0.45 avg
            no_shares=10,
            no_total_cost=4.8   # $0.48 avg
        )
        # Combined: (4.5 + 4.8) / 10 = 0.93
        self.assertAlmostEqual(pos.combined_avg_cost, 0.93, places=2)

    def test_is_complete_pair(self):
        """Test complete pair detection."""
        # Incomplete - no NO shares
        pos1 = ArbitragePosition(market_id="test", yes_shares=10, no_shares=0)
        self.assertFalse(pos1.is_complete_pair)

        # Complete - both sides
        pos2 = ArbitragePosition(market_id="test", yes_shares=10, no_shares=10)
        self.assertTrue(pos2.is_complete_pair)

    def test_guaranteed_profit(self):
        """Test profit calculation."""
        pos = ArbitragePosition(
            market_id="test",
            yes_shares=10,
            yes_total_cost=4.5,
            no_shares=10,
            no_total_cost=4.5
        )
        # Combined cost: 0.90, profit: 0.10 per pair
        self.assertAlmostEqual(pos.guaranteed_profit_per_pair, 0.10, places=2)

    def test_is_expired(self):
        """Test expiration detection."""
        # Not expired - just opened
        pos1 = ArbitragePosition(
            market_id="test",
            opened_at=datetime.now(),
            holding_time_limit=1800
        )
        self.assertFalse(pos1.is_expired)

        # Expired - opened long ago
        pos2 = ArbitragePosition(
            market_id="test",
            opened_at=datetime.now() - timedelta(hours=1),
            holding_time_limit=1800
        )
        self.assertTrue(pos2.is_expired)


class TestPositionTracker(unittest.TestCase):
    """Tests for PositionTracker class."""

    def setUp(self):
        """Set up test fixtures."""
        self.tracker = PositionTracker(max_concurrent=3)

    def test_add_yes_position(self):
        """Test adding YES position."""
        result = self.tracker.add_yes_position(
            market_id="market1",
            shares=10,
            cost=4.5
        )
        self.assertTrue(result)

        pos = self.tracker.get_position("market1")
        self.assertIsNotNone(pos)
        self.assertEqual(pos.yes_shares, 10)
        self.assertAlmostEqual(pos.yes_avg_cost, 0.45, places=2)

    def test_add_no_position(self):
        """Test adding NO position."""
        result = self.tracker.add_no_position(
            market_id="market1",
            shares=10,
            cost=4.8
        )
        self.assertTrue(result)

        pos = self.tracker.get_position("market1")
        self.assertEqual(pos.no_shares, 10)

    def test_max_concurrent_limit(self):
        """Test concurrent position limit enforcement."""
        # Add 3 positions (max)
        for i in range(3):
            self.tracker.add_yes_position(f"market{i}", 10, 4.5)

        # 4th should fail
        result = self.tracker.add_yes_position("market3", 10, 4.5)
        self.assertFalse(result)

    def test_get_complete_pairs(self):
        """Test getting complete pairs."""
        # Add incomplete position
        self.tracker.add_yes_position("market1", 10, 4.5)

        # Add complete position
        self.tracker.add_yes_position("market2", 10, 4.5)
        self.tracker.add_no_position("market2", 10, 4.5)

        complete = self.tracker.get_complete_pairs()
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0].market_id, "market2")

    def test_get_incomplete_pairs(self):
        """Test getting incomplete pairs."""
        self.tracker.add_yes_position("market1", 10, 4.5)

        incomplete = self.tracker.get_incomplete_pairs()
        self.assertEqual(len(incomplete), 1)
        self.assertEqual(incomplete[0].market_id, "market1")

    def test_thread_safety(self):
        """Test thread-safe operations."""
        errors = []

        def add_positions():
            try:
                for i in range(10):
                    self.tracker.add_yes_position(f"thread_market_{i}", 1, 0.5)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_positions) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

    def test_mark_resolved(self):
        """Test marking position as resolved."""
        self.tracker.add_yes_position("market1", 10, 4.5)
        self.tracker.add_no_position("market1", 10, 4.5)

        result = self.tracker.mark_resolved("market1", profit=0.10)
        self.assertTrue(result)

        pos = self.tracker.get_position("market1")
        self.assertTrue(pos.resolved)
        self.assertEqual(pos.profit, 0.10)


if __name__ == "__main__":
    unittest.main()
