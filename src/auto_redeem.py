#!/usr/bin/env python3
"""
Gabagool Bot - Auto Redeemer

Purpose:
    Automatically detect settled markets and redeem positions.
    Runs as background service, polling for resolved markets.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Based on: samples/lorine93s-mm/src/services/auto_redeem.py
    Uses Polymarket Data API for redeemable position checks.

Dependencies:
    - asyncio
    - aiohttp
    - logging

Usage:
    from src.auto_redeem import AutoRedeemer

    redeemer = AutoRedeemer(wallet_address, position_tracker, stats_tracker)

    # Run as background task
    task = asyncio.create_task(redeemer.run_continuous())

    # Or check once
    redeemed = await redeemer.check_and_redeem()

Notes:
    - Polls every 5 minutes by default
    - Uses Polymarket Data API for position queries
    - Updates position tracker and stats tracker on redemption
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import aiohttp


class AutoRedeemer:
    """
    Automated position redemption service.

    Based on lorine93s/polymarket-market-maker-bot auto_redeem.py.
    Checks for redeemable positions via Polymarket Data API and redeems them.
    """

    # Polymarket Data API endpoints
    DATA_API_URL = "https://data-api.polymarket.com"

    def __init__(
        self,
        wallet_address: str,
        position_tracker: Any = None,
        stats_tracker: Any = None,
        check_interval: int = 300,  # 5 minutes
        redeem_threshold_usd: float = 0.10,  # Minimum value to redeem
        enabled: bool = True
    ):
        """
        Initialize auto-redeemer.

        Args:
            wallet_address: Polymarket wallet address to check positions for
            position_tracker: Optional PositionTracker instance
            stats_tracker: Optional StatsTracker instance
            check_interval: Seconds between checks (default 300)
            redeem_threshold_usd: Minimum USD value to trigger redemption
            enabled: Whether auto-redemption is enabled
        """
        self.wallet_address = wallet_address
        self.position_tracker = position_tracker
        self.stats_tracker = stats_tracker
        self.check_interval = check_interval
        self.redeem_threshold_usd = redeem_threshold_usd
        self.enabled = enabled

        self.logger = logging.getLogger("auto_redeemer")
        self.running = False
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Web3 setup
        self.private_key = None
        self._web3 = None
        self._ctf_contract = None
        
        # We will initialize Web3 later when keys are available
        self._initialized = False

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    def initialize_web3(self, private_key: str, rpc_url: str = "https://polygon-rpc.com") -> bool:
        """Initialize Web3 connection for on-chain redemptions."""
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware
            
            self.private_key = private_key
            self._web3 = Web3(Web3.HTTPProvider(rpc_url))
            self._web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            
            # CTF Contract setup
            ctf_address = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
            ctf_abi = [{
                "inputs": [
                    {"name": "collateralToken", "type": "address"},
                    {"name": "parentCollectionId", "type": "bytes32"},
                    {"name": "conditionId", "type": "bytes32"},
                    {"name": "indexSets", "type": "uint256[]"}
                ],
                "name": "redeemPositions",
                "outputs": [],
                "stateMutability": "nonpayable",
                "type": "function"
            }]
            
            self._ctf_contract = self._web3.eth.contract(address=ctf_address, abi=ctf_abi)
            self._initialized = True
            self.logger.info("Web3 initialized for AutoRedeemer")
            return True
            
        except ImportError:
            self.logger.warning("web3 package not installed - on-chain redemption disabled")
            return False
        except Exception as e:
            self.logger.error("Failed to initialize Web3: %s", e)
            return False

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def get_redeemable_positions(self) -> List[Dict[str, Any]]:
        """
        Query Polymarket Data API for redeemable positions.

        Returns:
            List of redeemable position dicts
        """
        try:
            session = await self._get_session()
            url = f"{self.DATA_API_URL}/positions"
            params = {
                "user": self.wallet_address,
                "redeemable": "true"
            }

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    positions = await response.json()
                    self.logger.debug("Found %d redeemable positions", len(positions))
                    return positions
                else:
                    self.logger.warning(
                        "Failed to get redeemable positions: HTTP %d",
                        response.status
                    )
                    return []

        except Exception as e:
            self.logger.error("Error checking redeemable positions: %s", e)
            return []

    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """
        Get all positions for the wallet.

        Returns:
            List of position dicts
        """
        try:
            session = await self._get_session()
            url = f"{self.DATA_API_URL}/positions"
            params = {"user": self.wallet_address}

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                return []

        except Exception as e:
            self.logger.error("Error getting positions: %s", e)
            return []

    async def _fetch_parent_collection_id(self, condition_id: str) -> bytes:
        """
        Query the Gamma API to retrieve the parentCollectionId for a condition.

        Standard binary markets use b'\\x00' * 32 (the zero hash), but
        negative-risk or multi-outcome markets may use a non-zero parent
        collection. Falling back to the zero hash if the API is unavailable
        is safe for standard markets.

        Args:
            condition_id: The market condition ID (hex string)

        Returns:
            parentCollectionId as 32 bytes
        """
        try:
            session = await self._get_session()
            url = f"https://gamma-api.polymarket.com/markets"
            params = {"condition_id": condition_id}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    markets = data if isinstance(data, list) else data.get("markets", [])
                    if markets:
                        parent_hex = markets[0].get("parentCollectionId") or ""
                        if parent_hex and parent_hex != "0x" + "0" * 64:
                            from web3 import Web3
                            return Web3.to_bytes(hexstr=parent_hex)
        except Exception as e:
            self.logger.debug("Could not fetch parentCollectionId from Gamma: %s", e)
        return b'\x00' * 32

    async def redeem_position(self, condition_id: str) -> bool:
        """
        Attempt to redeem a specific position on-chain via Web3.

        Queries the Gamma API for the exact parentCollectionId before
        submitting the on-chain transaction. Falls back to manual API
        redemption if the contract call reverts (e.g. for negative-risk
        or multi-outcome markets that use a non-zero parent collection).

        Args:
            condition_id: The market's conditionId

        Returns:
            True if redemption transaction was submitted successfully
        """
        if not self._initialized or not self.private_key:
            self.logger.warning("Redemption requires Web3 initialization with private key")
            return False

        try:
            from web3 import Web3
            from eth_account import Account

            self.logger.info("Attempting on-chain redemption for condition: %s", condition_id)

            # Standard Polymarket collateral (USDC.e on Polygon)
            usdc = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")

            # Fetch the correct parentCollectionId from Gamma API.
            # Standard markets use zero bytes; negative-risk/multi-outcome
            # markets may require a non-zero parent collection.
            parent_collection = await self._fetch_parent_collection_id(condition_id)

            # Condition ID must be converted to bytes32
            cond_bytes = Web3.to_bytes(hexstr=condition_id)

            # Polymarket binary markets use index sets 1 (YES/UP) and 2 (NO/DOWN).
            # Calling redeem with both index sets covers both outcomes safely.
            index_sets = [1, 2]

            account = Account.from_key(self.private_key)

            try:
                tx = self._ctf_contract.functions.redeemPositions(
                    usdc,
                    parent_collection,
                    cond_bytes,
                    index_sets
                ).build_transaction({
                    'from': account.address,
                    'nonce': self._web3.eth.get_transaction_count(account.address),
                    'gas': 150000,
                    'maxFeePerGas': self._web3.to_wei(50, 'gwei'),
                    'maxPriorityFeePerGas': self._web3.to_wei(40, 'gwei'),
                })

                signed_tx = self._web3.eth.account.sign_transaction(tx, self.private_key)
                tx_hash = self._web3.eth.send_raw_transaction(signed_tx.raw_transaction)

                self.logger.info("Redemption TX submitted! Hash: %s", tx_hash.hex())
                return True

            except Exception as contract_err:
                self.logger.warning(
                    "On-chain redeem reverted for %s (%s) — "
                    "position requires manual or API-based redemption",
                    condition_id, contract_err,
                )
                return False

        except Exception as e:
            self.logger.error("On-chain redemption failed for %s: %s", condition_id, e)
            return False

    async def check_and_redeem(self) -> int:
        """
        Check for redeemable positions and attempt to redeem them.

        Returns:
            Number of positions processed
        """
        if not self.enabled:
            return 0

        redeemable = await self.get_redeemable_positions()
        processed = 0

        for position in redeemable:
            try:
                position_id = position.get("id", "")
                value_usd = float(position.get("value", 0))
                market_slug = position.get("slug", "unknown")

                # Skip positions below threshold
                if value_usd < self.redeem_threshold_usd:
                    self.logger.debug(
                        "Skipping %s (value $%.2f < threshold $%.2f)",
                        market_slug, value_usd, self.redeem_threshold_usd
                    )
                    continue

                self.logger.info(
                    "Found redeemable position: %s | Value: $%.2f",
                    market_slug, value_usd
                )

                # Attempt redemption using conditionId
                condition_id = position.get("conditionId", "")
                if not condition_id:
                    self.logger.warning("Missing conditionId for redeemable position")
                    continue
                    
                success = await self.redeem_position(condition_id)
                if success:
                    processed += 1

                    # Update stats if tracker is available
                    if self.stats_tracker:
                        self.stats_tracker.update_trade_result(
                            position.get("conditionId", market_slug),
                            "redeemed",
                            value_usd
                        )

            except Exception as e:
                self.logger.error("Error processing position: %s", e)

        if processed > 0:
            self.logger.info("Redeemed %d positions", processed)
        elif redeemable:
            self.logger.info(
                "Found %d redeemable positions (manual redemption required)",
                len(redeemable)
            )

        return processed

    async def run_continuous(self) -> None:
        """
        Run auto-redemption service continuously.
        Runs as background task, checking periodically.
        """
        self.running = True
        self.logger.info(
            "Auto-redemption service started (interval: %ds, threshold: $%.2f)",
            self.check_interval, self.redeem_threshold_usd
        )

        while self.running:
            try:
                await self.check_and_redeem()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                self.logger.info("Auto-redemption service cancelled")
                break
            except Exception as e:
                self.logger.error("Auto-redemption error: %s", e)
                await asyncio.sleep(60)  # Wait 1 min on error

        self.running = False
        await self.close()
        self.logger.info("Auto-redemption service stopped")

    def stop(self) -> None:
        """Stop the continuous redemption service."""
        self.running = False
        self.logger.info("Auto-redemption service stopping...")

    async def get_wallet_value(self) -> float:
        """
        Get total value of all positions.

        Returns:
            Total position value in USD
        """
        try:
            session = await self._get_session()
            url = f"{self.DATA_API_URL}/value"
            params = {"user": self.wallet_address}

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return float(data.get("value", 0))
                return 0.0

        except Exception as e:
            self.logger.error("Error getting wallet value: %s", e)
            return 0.0
