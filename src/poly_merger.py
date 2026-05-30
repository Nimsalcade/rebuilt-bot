#!/usr/bin/env python3
"""
Gabagool Bot - Position Merger (Gas Optimization)

Purpose:
    Consolidate YES + NO positions to reduce gas costs.
    Instead of selling separately, merge back into USDC.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Based on: samples/warproxxx-maker/poly_data/polymarket_client.py
    Reference: docs/developers/CTF/merge.md

Dependencies:
    - web3
    - eth-account
    - logging

Usage:
    from src.poly_merger import PositionMerger

    merger = PositionMerger(private_key, wallet_address)

    # Check if positions can be merged
    if merger.can_merge_positions(yes_shares, no_shares):
        # Calculate mergeable amount
        amount = merger.get_mergeable_amount(yes_shares, no_shares)

        # Merge positions (requires on-chain transaction)
        result = await merger.merge_positions(condition_id, amount)
        print(f"Merged: {result.amount_merged}")

Notes:
    - Merging saves ~45% gas vs selling separately
    - Requires CTF Exchange contract interaction on Polygon
    - One unit of YES + one unit of NO = one unit of collateral (USDC)
    - Gas estimates: Sell x2 = ~120k, Merge = ~65k, Savings = ~45%
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass


# Polygon contract addresses (mainnet)
POLYGON_CONTRACTS = {
    "conditional_tokens": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    "neg_risk_adapter": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
    "usdc": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
}

# Minimal ABI for mergePositions function
CONDITIONAL_TOKENS_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"}
        ],
        "name": "mergePositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"}
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]


@dataclass
class MergeResult:
    """Result of a position merge operation."""
    success: bool
    amount_merged: float
    gas_used: int
    gas_saved_estimate: float
    tx_hash: Optional[str] = None
    error: Optional[str] = None


class PositionMerger:
    """
    Position merger for gas optimization.

    Based on warproxxx/poly-maker implementation.
    Consolidates YES + NO positions into USDC via CTF Exchange.

    How Merging Works:
        - CTF (Conditional Token Framework) allows "merging" positions
        - When you hold equal amounts of YES and NO tokens
        - Calling mergePositions() burns both tokens
        - Returns the underlying collateral (USDC)

    Gas Savings:
        - Selling YES: ~60k gas
        - Selling NO: ~60k gas
        - Total separate: ~120k gas
        - Merging: ~65k gas
        - Savings: ~55k gas (~45%)
    """

    # Gas estimates for calculations
    GAS_PER_SELL = 60000
    GAS_PER_MERGE = 65000

    # USDC has 6 decimals
    USDC_DECIMALS = 6

    def __init__(
        self,
        private_key: Optional[str] = None,
        wallet_address: Optional[str] = None,
        rpc_url: str = "https://polygon-rpc.com",
        dry_run: bool = True
    ):
        """
        Initialize position merger.

        Args:
            private_key: Private key for signing transactions
            wallet_address: Wallet address (derived from key if not provided)
            rpc_url: Polygon RPC URL
            dry_run: If True, simulate transactions without executing
        """
        self.private_key = private_key
        self.wallet_address = wallet_address
        self.rpc_url = rpc_url
        self.dry_run = dry_run
        self.logger = logging.getLogger("position_merger")

        self._web3 = None
        self._ctf_contract = None
        self._initialized = False

        if private_key and not dry_run:
            self._initialize_web3()
        else:
            self.logger.info(
                "PositionMerger initialized (dry_run=%s, web3=%s)",
                dry_run, "not initialized" if not private_key else "ready"
            )

    def _initialize_web3(self) -> bool:
        """Initialize Web3 connection and contracts."""
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware
            from eth_account import Account

            self._web3 = Web3(Web3.HTTPProvider(self.rpc_url))
            self._web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            # Derive address from key if not provided
            if not self.wallet_address and self.private_key:
                account = Account.from_key(self.private_key)
                self.wallet_address = account.address

            # Initialize CTF contract
            self._ctf_contract = self._web3.eth.contract(
                address=Web3.to_checksum_address(POLYGON_CONTRACTS["conditional_tokens"]),
                abi=CONDITIONAL_TOKENS_ABI
            )

            self._initialized = True
            self.logger.info("Web3 initialized: %s", self.wallet_address[:10] if self.wallet_address else "N/A")
            return True

        except ImportError:
            self.logger.warning("web3 package not installed - merge functionality disabled")
            return False
        except Exception as e:
            self.logger.error("Failed to initialize Web3: %s", e)
            return False

    def can_merge_positions(
        self,
        yes_shares: float,
        no_shares: float,
        min_amount: float = 0.01
    ) -> bool:
        """
        Check if positions are eligible for merging.

        Args:
            yes_shares: Number of YES shares
            no_shares: Number of NO shares
            min_amount: Minimum shares to merge (default 0.01)

        Returns:
            True if positions can be merged
        """
        # Can only merge equal amounts of YES and NO
        mergeable = min(yes_shares, no_shares)
        return mergeable >= min_amount

    def get_mergeable_amount(
        self,
        yes_shares: float,
        no_shares: float
    ) -> float:
        """
        Get the amount that can be merged.

        Args:
            yes_shares: Number of YES shares
            no_shares: Number of NO shares

        Returns:
            Amount that can be merged (min of both sides)
        """
        return min(yes_shares, no_shares)

    def get_raw_balance(self, token_id: int) -> int:
        """
        Get raw token balance from CTF contract.

        Args:
            token_id: Token ID to check

        Returns:
            Raw balance (with decimals, divide by 1e6 for shares)
        """
        if not self._initialized:
            self.logger.warning("Web3 not initialized")
            return 0

        try:
            balance = self._ctf_contract.functions.balanceOf(
                self.wallet_address,
                token_id
            ).call()
            return balance
        except Exception as e:
            self.logger.error("Error getting balance: %s", e)
            return 0

    async def merge_positions(
        self,
        condition_id: str,
        amount: float,
        is_neg_risk: bool = False
    ) -> MergeResult:
        """
        Merge YES + NO positions back into USDC.

        This calls the CTF contract's mergePositions function:
        - Burns equal amounts of YES and NO tokens
        - Returns USDC collateral to wallet

        Args:
            condition_id: Market condition ID (bytes32 hex string)
            amount: Amount to merge (in shares, not raw)
            is_neg_risk: Whether this is a negative risk market

        Returns:
            MergeResult with status and details
        """
        self.logger.info(
            "Merging %.4f pairs for condition %s (neg_risk=%s)",
            amount, condition_id[:16] if condition_id else "N/A", is_neg_risk
        )

        # Calculate gas savings estimate
        gas_estimate = self.estimate_gas_savings(amount)

        if self.dry_run:
            self.logger.info(
                "DRY RUN: Would merge %.4f pairs, saving ~%d gas",
                amount, gas_estimate["savings_gas"]
            )
            return MergeResult(
                success=True,
                amount_merged=amount,
                gas_used=self.GAS_PER_MERGE,
                gas_saved_estimate=gas_estimate["savings_gas"],
                tx_hash="dry_run_" + (condition_id[:8] if condition_id else "test"),
                error=None
            )

        if not self._initialized:
            return MergeResult(
                success=False,
                amount_merged=0,
                gas_used=0,
                gas_saved_estimate=0,
                error="Web3 not initialized - call _initialize_web3() first"
            )

        try:
            from web3 import Web3

            # Convert amount to raw (with 6 decimals for USDC)
            raw_amount = int(amount * (10 ** self.USDC_DECIMALS))

            # Partition for binary market: [1, 2] (YES=1, NO=2)
            partition = [1, 2]

            # For neg_risk markets the NegRiskAdapter holds the collateral,
            # so its address must be used as the contract target. For standard
            # binary markets we use the ConditionalTokens contract directly.
            if is_neg_risk:
                from web3 import Web3 as _Web3
                contract_address = _Web3.to_checksum_address(POLYGON_CONTRACTS["neg_risk_adapter"])
                ctf = self._web3.eth.contract(
                    address=contract_address,
                    abi=CONDITIONAL_TOKENS_ABI,
                )
            else:
                ctf = self._ctf_contract

            # Derive account from the private key held by this instance.
            # The private_key attribute is populated by the caller via
            # KeyManager.load_and_decrypt() — it is never written to disk
            # in plaintext form by this class.
            account = self._web3.eth.account.from_key(self.private_key)
            nonce = self._web3.eth.get_transaction_count(account.address)

            tx = ctf.functions.mergePositions(
                Web3.to_checksum_address(POLYGON_CONTRACTS["usdc"]),
                b'\x00' * 32,
                Web3.to_bytes(hexstr=condition_id),
                partition,
                raw_amount,
            ).build_transaction({
                'from': account.address,
                'nonce': nonce,
                'gas': self.GAS_PER_MERGE + 10000,
                'maxFeePerGas': self._web3.to_wei(50, 'gwei'),
                'maxPriorityFeePerGas': self._web3.to_wei(40, 'gwei'),
            })

            signed_tx = self._web3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self._web3.eth.send_raw_transaction(signed_tx.raw_transaction)

            self.logger.info(
                "Merge TX submitted | condition=%s | amount=%.4f | tx=%s",
                condition_id[:16], amount, tx_hash.hex(),
            )

            return MergeResult(
                success=True,
                amount_merged=amount,
                gas_used=self.GAS_PER_MERGE,
                gas_saved_estimate=gas_estimate["savings_gas"],
                tx_hash=tx_hash.hex(),
            )

        except Exception as e:
            self.logger.error("Merge failed: %s", e)
            return MergeResult(
                success=False,
                amount_merged=0,
                gas_used=0,
                gas_saved_estimate=0,
                error=str(e)
            )

    def estimate_gas_savings(self, amount: float) -> Dict[str, Any]:
        """
        Estimate gas savings from merging vs selling separately.

        Args:
            amount: Number of pairs to merge/sell

        Returns:
            Dict with gas estimates and savings
        """
        # Selling YES and NO separately
        separate_gas = 2 * self.GAS_PER_SELL

        # Merging into USDC
        merge_gas = self.GAS_PER_MERGE

        # Savings
        savings_gas = separate_gas - merge_gas
        savings_pct = savings_gas / separate_gas if separate_gas > 0 else 0

        return {
            "separate_sell_gas": separate_gas,
            "merge_gas": merge_gas,
            "savings_gas": savings_gas,
            "savings_percent": savings_pct,
            "amount": amount
        }

    def print_gas_savings(self, amount: float = 1.0) -> None:
        """Print gas savings estimate to console."""
        est = self.estimate_gas_savings(amount)

        print("\n" + "=" * 40)
        print("GAS SAVINGS ESTIMATE")
        print("=" * 40)
        print(f"Amount: {est['amount']} pairs")
        print(f"Separate sells: {est['separate_sell_gas']:,} gas")
        print(f"Merge: {est['merge_gas']:,} gas")
        print(f"Savings: {est['savings_gas']:,} gas ({est['savings_percent']:.1%})")
        print("=" * 40 + "\n")

    def should_merge(
        self,
        yes_shares: float,
        no_shares: float,
        current_gas_price_gwei: float = 30,
        min_savings_usd: float = 0.01
    ) -> bool:
        """
        Determine if merging is worthwhile given current gas prices.

        Args:
            yes_shares: Number of YES shares
            no_shares: Number of NO shares
            current_gas_price_gwei: Current gas price in gwei
            min_savings_usd: Minimum savings to justify merge

        Returns:
            True if merging would save at least min_savings_usd
        """
        if not self.can_merge_positions(yes_shares, no_shares):
            return False

        # Estimate savings
        est = self.estimate_gas_savings(min(yes_shares, no_shares))

        # Convert gas savings to USD (rough estimate)
        # Gas price in gwei * gas saved * MATIC price (assume ~$0.50)
        matic_price_usd = 0.50
        savings_usd = (current_gas_price_gwei * 1e-9 * est["savings_gas"] * matic_price_usd)

        return savings_usd >= min_savings_usd
