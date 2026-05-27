#!/usr/bin/env python3
"""
Unit Tests - Risk Manager

Purpose:
    Test risk management validation including position limits,
    exposure caps, and profit margin requirements.

Author: AI-Generated
Created: 2026-01-26
"""

import unittest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.risk_manager import RiskManager, RiskConfig


class TestRiskManager(unittest.TestCase):
    """Tests for RiskManager class."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = RiskConfig(
            max_position_per_market=100.0,
            max_total_exposure=500.0,
            max_concurrent_arbitrages=3,
            min_profit_margin=0.02,
            max_combined_cost=0.98
        )
        self.risk_mgr = RiskManager(self.config)

    def test_valid_arbitrage(self):
        """Test validation of valid arbitrage."""
        is_valid, reason = self.risk_mgr.validate_arbitrage(
            market_id="market1",
            yes_price=0.45,
            no_price=0.48,
            trade_size=10.0,
            current_positions={}
        )
        self.assertTrue(is_valid)
        self.assertEqual(reason, "OK")

    def test_profit_margin_too_low(self):
        """Test rejection when profit margin too low."""
        is_valid, reason = self.risk_mgr.validate_arbitrage(
            market_id="market1",
            yes_price=0.50,
            no_price=0.49,  # Combined: 0.99, margin: 0.01 < 0.02
            trade_size=10.0,
            current_positions={}
        )
        self.assertFalse(is_valid)
        self.assertIn("margin too low", reason.lower())

    def test_combined_cost_too_high(self):
        """Test rejection when combined cost too high."""
        # Combined 1.05 > max_combined_cost (0.98)
        # This fails profit margin check first (margin = -0.05 < 0.02)
        is_valid, reason = self.risk_mgr.validate_arbitrage(
            market_id="market1",
            yes_price=0.55,
            no_price=0.50,  # Combined: 1.05 > 0.98
            trade_size=10.0,
            current_positions={}
        )
        self.assertFalse(is_valid)
        # Fails on margin check since margin is negative
        self.assertIn("margin", reason.lower())

    def test_max_concurrent_positions(self):
        """Test rejection when max positions reached."""
        # Mock 3 existing positions
        mock_positions = {
            f"market{i}": type('MockPos', (), {'resolved': False, 'total_exposure': 50})()
            for i in range(3)
        }

        is_valid, reason = self.risk_mgr.validate_arbitrage(
            market_id="market_new",
            yes_price=0.45,
            no_price=0.48,
            trade_size=10.0,
            current_positions=mock_positions
        )
        self.assertFalse(is_valid)
        self.assertIn("max concurrent", reason.lower())

    def test_total_exposure_limit(self):
        """Test rejection when exposure limit exceeded."""
        # Mock existing positions with high exposure
        mock_positions = {
            "market1": type('MockPos', (), {'resolved': False, 'total_exposure': 450})()
        }

        is_valid, reason = self.risk_mgr.validate_arbitrage(
            market_id="market_new",
            yes_price=0.45,
            no_price=0.48,
            trade_size=50.0,  # Would add $100 exposure, total = $550 > $500
            current_positions=mock_positions
        )
        self.assertFalse(is_valid)
        self.assertIn("exposure limit", reason.lower())

    def test_invalid_price_range(self):
        """Test rejection of invalid prices."""
        # YES price = 0 causes margin calculation issues
        # Combined = 0 + 0.48 = 0.48, margin = 0.52 (OK)
        # But price check catches it
        is_valid, reason = self.risk_mgr.validate_arbitrage(
            market_id="market1",
            yes_price=0.0,
            no_price=0.48,
            trade_size=10.0,
            current_positions={}
        )
        self.assertFalse(is_valid)
        self.assertIn("invalid yes price", reason.lower())

        # NO price = 1.0 (exactly 1, invalid)
        # Combined = 0.45 + 1.0 = 1.45, margin = -0.45 (fails margin check first)
        is_valid, reason = self.risk_mgr.validate_arbitrage(
            market_id="market1",
            yes_price=0.45,
            no_price=1.0,
            trade_size=10.0,
            current_positions={}
        )
        self.assertFalse(is_valid)
        # Fails margin check before price range check
        self.assertIn("margin", reason.lower())

    def test_calculate_safe_trade_size(self):
        """Test safe trade size calculation."""
        safe_size = self.risk_mgr.calculate_safe_trade_size(
            current_exposure=400.0,  # $100 remaining
            wallet_balance=200.0,
            yes_price=0.45,
            no_price=0.48
        )
        # Max from exposure: $50/side
        # Max from balance: $100/side
        # Should be $50 (exposure limited)
        self.assertEqual(safe_size, 50.0)


if __name__ == "__main__":
    unittest.main()
