#!/usr/bin/env python3
"""
Gabagool Bot - Merge Engine

PURPOSE
-------
Gabagool22's primary profit source was NOT winning directional bets.
It was merging opposing UP+DOWN positions back to USDC before expiry.

From the CSV forensics:
  - 54,595 trades deployed $230,078
  - 513 MERGE events returned $275,561   ← 97.7% of all revenue
  - 469 REDEEM events returned $4,063    ← 1.4% of all revenue

The old poly_merger.py used raw Web3 contract calls — wrong approach
for Polymarket's neg-risk markets and completely unwired from the loop.

This module uses the py-clob-client-v2 SDK merge endpoint directly,
which handles neg-risk routing automatically, and is wired into
MakerLoop._reconcile_fills() so it runs continuously during farming.

HOW IT WORKS
------------
After every fill reconciliation in MakerLoop, this engine checks:

  mergeable = min(up_shares_filled, down_shares_filled)

If mergeable >= MIN_MERGE_SHARES (0.5), it calls the CLOB client's
merge endpoint, which:
  1. Burns `mergeable` UP shares + `mergeable` DOWN shares
  2. Returns `mergeable` USDC to the wallet immediately

This gives us the same $0.01-0.05 per-pair spread capture that
Gabagool22 earned 513 times for $275K total.

INTEGRATION
-----------
Called from MakerLoop after every _reconcile_fills() run.
Uses the same bot.clob client already authenticated in TradingBot.

Example:
    engine = MergeEngine(bot, dry_run=True)
    merged = await engine.try_merge(market, summary)
    # merged = USDC amount returned, or 0.0 if nothing to merge
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Any

import src.terminal_ui as terminal_ui

# Minimum shares to bother merging (below this the gas cost isn't worth it)
# Lowered to 10.0 to capture spread profit more frequently at higher capital levels
MIN_MERGE_SHARES = 10.0

# How often to attempt merges per window (seconds) — don't spam the chain
MERGE_COOLDOWN_S = 2.0




@dataclass
class MergeResult:
    success: bool
    merged_shares: float
    usdc_returned: float   # 1 share pair = $1.00 USDC
    error: Optional[str] = None
    tx_hash: Optional[str] = None


class MergeEngine:
    """
    Continuously merges opposing UP+DOWN fills into USDC.

    This is the core profit mechanism — buy both sides below $1.00 combined,
    merge immediately at $1.00, pocket the spread.
    """

    def __init__(self, bot: Any, dry_run: bool = False):
        self.bot = bot
        self.dry_run = dry_run
        self.logger = logging.getLogger("merge_engine")
        self._last_merge_at: float = 0.0
        self._total_merged_usdc: float = 0.0
        self._total_merge_count: int = 0

    async def try_merge(
        self,
        market: Any,
        summary: Any,  # WindowFillSummary
        force: bool = False,
    ) -> MergeResult:
        """
        Check if we have mergeable UP+DOWN pairs and execute merge.

        Args:
            market:  Market object with yes_token_id, no_token_id, id
            summary: WindowFillSummary with up_shares, down_shares fill counts

        Returns:
            MergeResult with amount merged and USDC returned
        """
        now = time.time()

        # Rate-limit: don't merge more than once per MERGE_COOLDOWN_S
        if (now - self._last_merge_at) < MERGE_COOLDOWN_S:
            return MergeResult(success=False, merged_shares=0, usdc_returned=0,
                               error="cooldown")

        up_shares = summary.up_shares
        down_shares = summary.down_shares
        mergeable = min(up_shares, down_shares)

        if mergeable < MIN_MERGE_SHARES and not force:
            return MergeResult(success=False, merged_shares=0, usdc_returned=0,
                               error=f"not enough to merge ({mergeable:.2f} < {MIN_MERGE_SHARES})")

        self._last_merge_at = now
        condition_id = market.condition_id

        if self.dry_run:
            # We deduct the merged shares, but DO NOT reduce total_cost.
            # Preserving the total_cost is required to accurately calculate Net PnL.
            summary.up_shares -= mergeable
            summary.down_shares -= mergeable
            
            usdc = mergeable  # 1 pair = $1.00
            self._total_merged_usdc += usdc
            self._total_merge_count += 1
            
            summary.merged_usdc += usdc
            
            print(terminal_ui.fmt_merge(summary.market_id, mergeable, usdc), flush=True)
            return MergeResult(success=True, merged_shares=mergeable, usdc_returned=usdc)

        # Live merge via unified SDK client
        try:
            result = await asyncio.to_thread(
                self._execute_merge_unified_sdk,
                condition_id,
                mergeable,
            )

            if result.success:
                # Deduct merged shares from the summary tracking
                summary.up_shares -= result.merged_shares
                summary.down_shares -= result.merged_shares
                summary.merged_usdc += result.usdc_returned
                self._total_merged_usdc += result.usdc_returned
                self._total_merge_count += 1
                
                # Instantly credit the merged USDC to the wallet balance cache to unthrottle redeployment
                if getattr(self.bot, '_cached_balance_micro', None) is not None:
                    self.bot._cached_balance_micro += int(result.usdc_returned * 1_000_000)
                    
                print(terminal_ui.fmt_merge(summary.market_id, result.merged_shares, result.usdc_returned), flush=True)
            else:
                self.logger.warning("MERGE FAILED: %s", result.error)

            return result

        except Exception as e:
            self.logger.error("Merge exception: %s", e)
            return MergeResult(success=False, merged_shares=0, usdc_returned=0,
                               error=str(e))

    def _execute_merge_unified_sdk(
        self,
        condition_id: str,
        amount: float | str,
    ) -> MergeResult:
        """Execute a true on-chain gasless merge using the unified polymarket-client.
        
        This SDK correctly supports Deposit Wallets (POLY_1271) via WALLET batches
        on the Relayer, ensuring guaranteed exactly $1.00 payouts per pair without
        CLOB slippage.
        """
        import asyncio
        from polymarket import AsyncSecureClient, BuilderApiKey

        async def _do_merge():
            current_amount = amount
            for attempt in range(2):
                secure_client = None
                gasless_client = None
                try:
                    self.logger.info("⛽ Initiating GASLESS on-chain merge via Unified SDK...")
                    
                    # Create base client
                    from py_clob_client.client import AsyncSecureClient
                    from py_clob_client.credentials import BuilderApiKey
                    
                    secure_client = await AsyncSecureClient.create(
                        private_key=self.bot.config.private_key,
                        wallet=self.bot.config.safe_address,
                        api_key=BuilderApiKey(
                            key=self.bot.config.builder_api_key,
                            secret=self.bot.config.builder_api_secret,
                            passphrase=self.bot.config.builder_api_passphrase,
                        ),
                    )
                    
                    # Bind to deposit wallet
                    gasless_client = await secure_client.setup_gasless_wallet()
                    
                    # Execute merge (amount must be in base units: 6 decimals, or "max")
                    merge_arg = "max" if current_amount == "max" else int(float(current_amount) * 1_000_000)
                    handle = await gasless_client.merge_positions(
                        condition_id=condition_id,
                        amount=merge_arg,
                    )
                    
                    self.logger.info("⏳ Merge submitted to Relayer. Waiting for chain confirmation...")
                    outcome = await handle.wait()
                    
                    await gasless_client.close()
                    await secure_client.close()
                    
                    if outcome and outcome.transaction_hash:
                        self.logger.info("✅ GASLESS MERGE MINED | tx_hash=%s", outcome.transaction_hash)
                        return MergeResult(success=True, merged_shares=current_amount, usdc_returned=current_amount, tx_hash=outcome.transaction_hash)
                    else:
                        self.logger.warning("❌ GASLESS MERGE FAILED (No tx hash returned)")
                        return MergeResult(success=False, merged_shares=0, usdc_returned=0, error="No tx hash returned")
                        
                except Exception as e:
                    if gasless_client:
                        try:
                            await gasless_client.close()
                        except: pass
                    if secure_client:
                        try:
                            await secure_client.close()
                        except: pass
                        
                    error_msg = str(e)
                    import re
                    _MAX_MERGEABLE_RE = re.compile(r'maximum mergeable amount\s+(\d+)', re.IGNORECASE)
                    match = _MAX_MERGEABLE_RE.search(error_msg)
                    if attempt == 0 and match and "exceeds the maximum" in error_msg:
                        max_amount_micro = int(match.group(1))
                        safe_micro = int(max_amount_micro * 0.99) # 1% haircut
                        safe_shares = safe_micro / 1_000_000.0
                        
                        if current_amount != "max":
                            if safe_shares >= MIN_MERGE_SHARES:
                                self.logger.warning("Merge amount exceeded actual balance. Retrying with clamped amount: %.2f", safe_shares)
                                current_amount = safe_shares
                                continue
                            else:
                                self.logger.warning("Clamped amount %.2f is below minimum %.2f, aborting merge.", safe_shares, MIN_MERGE_SHARES)
                                return MergeResult(success=False, merged_shares=0, usdc_returned=0, error=f"Clamped amount {safe_shares} below minimum")
                                
                    self.logger.error("Unified SDK merge exception: %s", e)
                    return MergeResult(success=False, merged_shares=0, usdc_returned=0, error=str(e))

        # We are running inside a thread (via asyncio.to_thread). We can use
        # asyncio.run() to spin up an isolated event loop for the SDK.
        return asyncio.run(_do_merge())


    def log_session_summary(self) -> None:
        """Log total merges for this session."""
        self.logger.info(
            "📊 MERGE SESSION SUMMARY | total_merged=$%.2f | merge_count=%d",
            self._total_merged_usdc, self._total_merge_count
        )
