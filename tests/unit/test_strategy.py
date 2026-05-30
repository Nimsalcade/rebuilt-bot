#!/usr/bin/env python3
"""
Unit Tests - Gabagool Strategy

Purpose:
    Test arbitrage opportunity detection and execution logic.

Author: AI-Generated
Created: 2026-01-26
"""

import unittest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from strategies.gabagool_strategy import GabagoolStrategy, StrategyConfig


class TestStrategyConfig(unittest.TestCase):
    """Tests for StrategyConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = StrategyConfig()
        self.assertEqual(config.yes_threshold, 0.48)
        self.assertEqual(config.no_threshold, 0.48)
        self.assertEqual(config.max_combined_cost, 0.97)
        self.assertEqual(config.min_profit_margin, 0.02)
        self.assertEqual(config.trade_size, 5.0)
        self.assertEqual(config.assets, ["BTC", "ETH", "SOL"])

    def test_custom_config(self):
        """Test custom configuration."""
        config = StrategyConfig(
            yes_threshold=0.45,
            no_threshold=0.45,
            trade_size=10.0,
            assets=["BTC"]
        )
        self.assertEqual(config.yes_threshold, 0.45)
        self.assertEqual(config.trade_size, 10.0)
        self.assertEqual(config.assets, ["BTC"])


class TestGabagoolStrategy(unittest.TestCase):
    """Tests for GabagoolStrategy class."""

    def setUp(self):
        """Set up test fixtures with mocks."""
        # Create mock objects
        self.mock_bot = type('MockBot', (), {})()
        self.mock_position_tracker = type('MockTracker', (), {
            'active_positions': {},
            'get_summary': lambda self: {"total_positions": 0}
        })()
        self.mock_risk_manager = type('MockRisk', (), {
            'validate_arbitrage': lambda self, *args, **kwargs: (True, "OK")
        })()
        self.mock_stats_tracker = type('MockStats', (), {
            'record_trade': lambda self, *args, **kwargs: None,
            'get_performance_summary': lambda self: {}
        })()
        self.mock_db = type('MockDB', (), {
            'save_trade': lambda self, *args, **kwargs: True,
            'save_position': lambda self, *args: True
        })()

        # Use thresholds that allow prices below them
        self.config = {
            "yes_threshold": 0.50,  # Prices must be < 0.50
            "no_threshold": 0.50,   # Prices must be < 0.50
            "max_combined_cost": 0.97,
            "min_profit_margin": 0.02,
            "trade_size": 5.0
        }

        self.strategy = GabagoolStrategy(
            bot=self.mock_bot,
            config=self.config,
            position_tracker=self.mock_position_tracker,
            risk_manager=self.mock_risk_manager,
            stats_tracker=self.mock_stats_tracker,
            db=self.mock_db
        )

    def test_is_opportunity_valid(self):
        """Test valid opportunity detection."""
        # Good opportunity: both prices < threshold (0.50), combined cost = 0.90
        # Margin = 1.0 - 0.90 = 0.10 > 0.02 (min_profit_margin)
        result = self.strategy._is_opportunity(0.45, 0.45)
        self.assertTrue(result)

    def test_is_opportunity_yes_too_high(self):
        """Test rejection when YES price too high (>= threshold)."""
        result = self.strategy._is_opportunity(0.55, 0.40)  # 0.55 >= 0.50
        self.assertFalse(result)

    def test_is_opportunity_no_too_high(self):
        """Test rejection when NO price too high (>= threshold)."""
        result = self.strategy._is_opportunity(0.40, 0.55)  # 0.55 >= 0.50
        self.assertFalse(result)

    def test_is_opportunity_combined_too_high(self):
        """Test rejection when combined cost too high."""
        # Both below threshold, combined 0.94 < 0.97 - OK
        result = self.strategy._is_opportunity(0.47, 0.47)
        self.assertTrue(result)

        # Both at threshold (0.50), so rejected because >= threshold
        result = self.strategy._is_opportunity(0.50, 0.50)
        self.assertFalse(result)

    def test_is_opportunity_margin_too_low(self):
        """Test rejection when profit margin too low."""
        # Combined 0.94, margin 0.06 > 0.02 - OK
        result = self.strategy._is_opportunity(0.47, 0.47)
        self.assertTrue(result)

        # Combined 0.96, margin 0.04 > 0.02 - still OK
        result = self.strategy._is_opportunity(0.48, 0.48)
        self.assertTrue(result)

        # Combined 0.99 > 0.97, rejected by combined cost check
        result = self.strategy._is_opportunity(0.495, 0.495)
        self.assertFalse(result)

    def test_get_status(self):
        """Test status summary."""
        status = self.strategy.get_status()
        self.assertEqual(status["strategy"], "gabagool")
        self.assertIn("config", status)
        self.assertIn("positions", status)
        self.assertIn("stats", status)


if __name__ == "__main__":
    unittest.main()
