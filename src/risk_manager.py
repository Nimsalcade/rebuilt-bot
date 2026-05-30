#!/usr/bin/env python3
"""
Gabagool Bot - Risk Manager

Purpose:
    Pre-trade validation framework to prevent losses.
    Validates trades against position limits, exposure caps,
    and profit margin requirements.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Based on: samples/lorine93s-mm/src/risk/risk_manager.py
    Key patterns to extract:
        - Pre-trade validation checks
        - Position limit enforcement
        - Exposure tracking

Dependencies:
    - dataclasses
    - logging

Usage:
    from src.risk_manager import RiskManager, RiskConfig

    config = RiskConfig(max_total_exposure=500.0)
    risk_mgr = RiskManager(config)

    is_valid, reason = risk_mgr.validate_arbitrage(
        market_id, yes_price, no_price, trade_size, positions
    )

Notes:
    - All trades must pass validation before execution
    - Returns (is_valid, reason) tuple for logging
    - Configurable via RiskConfig dataclass
"""

import logging
from dataclasses import dataclass
from typing import Tuple, Dict, Any


@dataclass
class RiskConfig:
    """
    Risk management configuration parameters.

    Attributes:
        max_position_per_market: Maximum USD per single market
        max_total_exposure: Maximum total USD across all positions
        max_concurrent_arbitrages: Maximum simultaneous arbitrage positions
        min_profit_margin: Minimum profit margin to enter (e.g., 0.02 = 2%)
        max_combined_cost: Maximum combined cost for YES + NO (e.g., 0.98)
        max_slippage: Maximum allowed slippage (e.g., 0.03 = 3%)
        max_position_age_minutes: Maximum holding time in minutes
        min_liquidity_per_side: Minimum liquidity required per side
    """
    max_position_per_market: float = 100.0
    max_total_exposure: float = 500.0
    max_concurrent_arbitrages: int = 3
    min_profit_margin: float = 0.02
    max_combined_cost: float = 0.98
    max_slippage: float = 0.03
    max_position_age_minutes: int = 30
    min_liquidity_per_side: float = 100.0


class RiskManager:
    """
    Pre-trade risk validation framework.

    Based on lorine93s/polymarket-market-maker-bot risk manager.
    Validates all trades before execution to prevent losses.
    """

    def __init__(self, config: RiskConfig):
        """
        Initialize risk manager.

        Args:
            config: RiskConfig with risk parameters
        """
        self.config = config
        self.logger = logging.getLogger("risk_manager")

    def validate_arbitrage(
        self,
        market_id: str,
        yes_price: float,
        no_price: float,
        trade_size: float,
        current_positions: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Comprehensive pre-trade validation for arbitrage.

        Args:
            market_id: Target market identifier
            yes_price: Current YES price
            no_price: Current NO price
            trade_size: USD amount per side
            current_positions: Dict of current ArbitragePosition objects

        Returns:
            Tuple of (is_valid, reason_string)
        """
        # Check 1: Position count limit
        active_count = sum(
            1 for pos in current_positions.values()
            if not getattr(pos, 'resolved', False)
        )
        if active_count >= self.config.max_concurrent_arbitrages:
            return False, f"Max concurrent positions ({self.config.max_concurrent_arbitrages}) reached"

        # Check 2: Total exposure limit
        current_exposure = sum(
            getattr(pos, 'total_exposure', 0)
            for pos in current_positions.values()
            if not getattr(pos, 'resolved', False)
        )
        new_exposure = 2 * trade_size  # YES side + NO side
        if current_exposure + new_exposure > self.config.max_total_exposure:
            return False, (
                f"Would exceed total exposure limit: "
                f"${current_exposure + new_exposure:.2f} > ${self.config.max_total_exposure:.2f}"
            )

        # Check 3: Profit margin validation
        combined_cost = yes_price + no_price
        profit_margin = 1.0 - combined_cost
        if profit_margin < self.config.min_profit_margin:
            return False, (
                f"Profit margin too low: {profit_margin:.3f} < {self.config.min_profit_margin:.3f}"
            )

        # Check 4: Max combined cost
        if combined_cost > self.config.max_combined_cost:
            return False, f"Combined cost too high: ${combined_cost:.4f} > ${self.config.max_combined_cost:.4f}"

        # Check 5: Price sanity checks
        if yes_price <= 0 or yes_price >= 1.0:
            return False, f"Invalid YES price: ${yes_price:.4f} (must be 0 < price < 1)"

        if no_price <= 0 or no_price >= 1.0:
            return False, f"Invalid NO price: ${no_price:.4f} (must be 0 < price < 1)"

        # Check 6: Per-market position limit
        if market_id in current_positions:
            existing = current_positions[market_id]
            existing_exposure = getattr(existing, 'total_exposure', 0)
            if existing_exposure + new_exposure > self.config.max_position_per_market:
                return False, (
                    f"Would exceed per-market limit: "
                    f"${existing_exposure + new_exposure:.2f} > ${self.config.max_position_per_market:.2f}"
                )

        # All checks passed
        self.logger.info(
            "Risk validation PASSED: %s | Profit margin: %.2f%% | Exposure: $%.2f",
            market_id[:16], profit_margin * 100, new_exposure
        )
        return True, "OK"

    def check_position_health(self, position: Any) -> Tuple[bool, str]:
        """
        Check if an existing position is healthy.

        Args:
            position: ArbitragePosition object

        Returns:
            Tuple of (is_healthy, reason_string)
        """
        # Check age
        if getattr(position, 'is_expired', False):
            return False, "Position expired (exceeded holding time limit)"

        # Check if complete pair formed
        if getattr(position, 'is_complete_pair', False):
            profit = getattr(position, 'guaranteed_profit_per_pair', 0)
            if profit > 0:
                return True, f"Complete arbitrage with ${profit:.4f} profit/pair"
            else:
                return False, f"Complete but unprofitable: ${profit:.4f}/pair"

        # Incomplete pair within time limits
        time_remaining = getattr(position, 'time_remaining', None)
        if time_remaining:
            return True, f"Incomplete pair, {time_remaining} remaining"

        return True, "Incomplete pair, within time limits"

    def check_circuit_breakers(
        self,
        daily_pnl: float,
        starting_balance: float,
        consecutive_failures: int,
        wallet_balance: float
    ) -> Tuple[bool, str]:
        """
        Check circuit breaker conditions.

        Args:
            daily_pnl: Today's profit/loss in USD
            starting_balance: Starting balance for the day
            consecutive_failures: Number of consecutive failed trades
            wallet_balance: Current wallet balance

        Returns:
            Tuple of (should_continue, reason_string)
        """
        # Check consecutive failures
        if consecutive_failures >= 3:
            return False, f"Circuit breaker: {consecutive_failures} consecutive failures"

        # Check daily drawdown
        if starting_balance > 0:
            drawdown = -daily_pnl / starting_balance
            if drawdown > 0.15:  # 15% max drawdown
                return False, f"Circuit breaker: Daily drawdown {drawdown:.1%} exceeds 15%"

        # Check minimum balance
        min_balance = 10.0  # $10 minimum
        if wallet_balance < min_balance:
            return False, f"Circuit breaker: Wallet balance ${wallet_balance:.2f} below minimum"

        return True, "OK"

    def calculate_safe_trade_size(
        self,
        current_exposure: float,
        wallet_balance: float,
        yes_price: float,
        no_price: float
    ) -> float:
        """
        Calculate maximum safe trade size given current state.

        Args:
            current_exposure: Current total exposure
            wallet_balance: Available wallet balance
            yes_price: YES price
            no_price: NO price

        Returns:
            Maximum safe trade size per side
        """
        # Remaining exposure capacity
        remaining_exposure = self.config.max_total_exposure - current_exposure
        max_per_side = remaining_exposure / 2

        # Don't exceed available balance
        max_per_side = min(max_per_side, wallet_balance / 2)

        # Don't exceed per-market limit
        max_per_side = min(max_per_side, self.config.max_position_per_market / 2)

        return max(0, max_per_side)
