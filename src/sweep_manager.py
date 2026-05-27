#!/usr/bin/env python3
import asyncio
import logging
import requests
import time
from typing import Optional

try:
    from web3 import Web3
    from eth_account import Account
except ImportError:
    Web3 = None

class SweepManager:
    """
    Handles automated profit sweeps from the Polymarket wallet to an external address.
    Uses the Polymarket Bridge API to generate a gateway address, then uses Web3.py
    to push the USDC.e to the gateway.
    """
    
    # USDC.e on Polygon
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    POLYGON_CHAIN_ID = 137

    def __init__(self, bot, config, logger=None):
        self.bot = bot
        self.config = config.sweeper
        self.safe_address = config.safe_address
        self.private_key = config.get_private_key()
        self.rpc_url = config.rpc_url
        self.logger = logger or logging.getLogger("sweep_manager")
        
        self.has_done_test = not self.config.do_test_transfer
        
        if Web3 is not None:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        else:
            self.w3 = None

    async def check_and_sweep(self, current_balance: float) -> float:
        """
        Evaluates the current balance against the threshold and executes a sweep if needed.
        Returns the new balance after the sweep.
        """
        if not self.config.enabled:
            return current_balance

        if current_balance < self.config.sweep_threshold:
            return current_balance

        amount_to_sweep = current_balance - self.config.reserve_bankroll
        
        # Safety limit for slippage protection (from tutorial)
        if amount_to_sweep > 50000.0:
            amount_to_sweep = 50000.0 

        self.logger.info("=" * 60)
        self.logger.info("🧹 PROFIT SWEEPER INITIATED")
        self.logger.info(f"Balance: ${current_balance:.2f} | Threshold: ${self.config.sweep_threshold:.2f}")
        
        if not self.has_done_test:
            self.logger.info("Running $1.00 safety test transfer first...")
            success = await self._execute_sweep(1.0)
            if success:
                self.has_done_test = True
                self.logger.info("Test successful. Waiting 5 minutes for exchange confirmation...")
                await asyncio.sleep(300)
                
                # Recalculate remaining sweep
                amount_to_sweep -= 1.0
                if amount_to_sweep > 0:
                    self.logger.info(f"Proceeding with main sweep of ${amount_to_sweep:.2f}...")
                    await self._execute_sweep(amount_to_sweep)
            else:
                self.logger.error("Test transfer failed. Aborting sweep.")
                return current_balance
        else:
            self.logger.info(f"Executing sweep of ${amount_to_sweep:.2f}...")
            await self._execute_sweep(amount_to_sweep)

        self.logger.info("=" * 60)
        # We assume the sweep was successful and balance is now the reserve.
        # The CapitalManager will poll the true balance anyway.
        return self.config.reserve_bankroll

    async def _execute_sweep(self, amount: float) -> bool:
        """Registers the withdrawal and broadcasts the ERC20 transfer."""
        gateway_address = await self._register_withdrawal()
        if not gateway_address:
            return False
            
        success = await self._broadcast_erc20_transfer(gateway_address, amount)
        return success

    async def _register_withdrawal(self) -> Optional[str]:
        """Calls the bridge API to get the deposit gateway address."""
        url = "https://bridge.polymarket.com/withdraw"
        payload = {
            "address": self.safe_address,
            "toChainId": str(self.POLYGON_CHAIN_ID),
            "toTokenAddress": self.USDC_ADDRESS,
            "recipientAddr": self.config.target_address
        }
        headers = {"Content-Type": "application/json"}
        
        try:
            # We use asyncio.to_thread to avoid blocking the event loop
            response = await asyncio.to_thread(
                requests.post, url, json=payload, headers=headers, timeout=10
            )
            response.raise_for_status()
            data = response.json()
            gateway = data.get('address')
            self.logger.info(f"Bridge API registered. Target Gateway: {gateway}")
            return gateway
        except Exception as err:
            self.logger.error(f"Bridge registration failed: {err}")
            return None

    async def _broadcast_erc20_transfer(self, to_address: str, amount_usd: float) -> bool:
        """Signs and broadcasts the ERC20 transfer using Web3.py."""
        if not self.w3 or not self.w3.is_connected():
            self.logger.error("Web3 is not connected or installed.")
            return False

        try:
            account = Account.from_key(self.private_key)
            wallet_address = account.address

            # USDC.e uses 6 decimals
            amount_wei = int(amount_usd * 1_000_000)

            # Minimal ERC20 ABI for transfer
            erc20_abi = [{
                "constant": False,
                "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
                "name": "transfer",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function"
            }]
            
            usdc_contract = self.w3.eth.contract(address=self.USDC_ADDRESS, abi=erc20_abi)
            
            # Note: If the user's funds are in a Gnosis Safe Proxy, a simple EOA transfer won't work here.
            # They would need to route through py_clob_client or an execTransaction payload.
            # Assuming the bot's private key directly controls the EOA funds per the tutorial instructions:
            
            tx = usdc_contract.functions.transfer(to_address, amount_wei).build_transaction({
                'chainId': self.POLYGON_CHAIN_ID,
                'gas': 100000,
                'maxFeePerGas': self.w3.eth.gas_price * 2,
                'maxPriorityFeePerGas': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(wallet_address),
            })
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction) # Use raw_transaction in newer web3 versions
            
            self.logger.info(f"Transaction broadcast! Hash: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = await asyncio.to_thread(self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=120)
            if receipt.status == 1:
                self.logger.info(f"✅ Sweep of ${amount_usd:.2f} confirmed on-chain!")
                return True
            else:
                self.logger.error("❌ Sweep transaction failed on-chain.")
                return False
                
        except Exception as e:
            self.logger.error(f"Error broadcasting sweep transaction: {e}")
            return False
