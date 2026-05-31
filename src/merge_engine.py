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
from typing import Optional, Any, Union

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
        # Circuit breaker: once the builder credentials are proven invalid the
        # relayer will reject EVERY merge identically, so retrying every ~2s
        # just floods the log (hundreds of identical lines in the live run).
        # Trip this on the first auth failure, log once, and stop hammering.
        self._merge_disabled: bool = False
        self._merge_disabled_reason: str = ""
        # Validate builder creds once at startup so a missing/blank triple is a
        # single clear message naming the exact env vars — not a per-merge spam.
        if not dry_run:
            self._check_builder_creds_at_startup()

    def _check_builder_creds_at_startup(self) -> None:
        """Validate the BUILDER_API_* triple before any merge is attempted.

        The gasless relayer authenticates via BuilderApiKeyCreds(key, secret,
        passphrase), issued on Polymarket's *Builders* settings tab and shown
        only at key-creation time. The signing SDK rejects any blank field, so
        catch that here once with an actionable message instead of letting it
        raise on every merge.
        """
        cfg = self.bot.config
        missing = [
            name for name, val in (
                ("BUILDER_API_KEY", getattr(cfg, "builder_api_key", "")),
                ("BUILDER_API_SECRET", getattr(cfg, "builder_api_secret", "")),
                ("BUILDER_API_PASSPHRASE", getattr(cfg, "builder_api_passphrase", "")),
            )
            if not (val or "").strip()
        ]
        if missing:
            self._merge_disabled = True
            self._merge_disabled_reason = "missing builder credentials: " + ", ".join(missing)
            self.logger.error(
                "MERGE DISABLED — %s. Set these in .env from Polymarket Settings → "
                "Builders → Create New (key/secret/passphrase are shown only at "
                "creation time). Auto-merge is OFF until they are present.",
                self._merge_disabled_reason,
            )

    @staticmethod
    def _is_auth_error(error: Optional[str]) -> bool:
        """True when a merge failure is a permanent credential/auth rejection."""
        if not error:
            return False
        e = error.lower()
        return (
            "builder credential" in e          # "invalid local builder credentials!"
            or "builder creds" in e            # "invalid builder creds configured!"
            or "could not generate builder headers" in e
            or "unauthorized" in e
            or "401" in e
        )

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

        # Circuit breaker: if builder creds are known-bad, do nothing (already
        # logged once at startup / first failure). Prevents the per-cycle flood.
        if self._merge_disabled:
            return MergeResult(success=False, merged_shares=0, usdc_returned=0,
                               error=self._merge_disabled_reason)

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

                # Instantly credit the merged USDC to the pending proceeds accumulator to unthrottle redeployment
                if hasattr(self.bot, 'pending_merge_proceeds'):
                    self.bot.pending_merge_proceeds += result.usdc_returned

                print(terminal_ui.fmt_merge(summary.market_id, result.merged_shares, result.usdc_returned), flush=True)
            else:
                # An auth/credential failure is permanent for this process — the
                # relayer will reject every subsequent merge the same way. Trip
                # the breaker so we log it once instead of every ~2s.
                if self._is_auth_error(result.error):
                    self._merge_disabled = True
                    self._merge_disabled_reason = result.error or "invalid builder credentials"
                    self.logger.error(
                        "MERGE DISABLED — relayer rejected builder credentials (%s). "
                        "Re-create the key on Polymarket Settings → Builders and set "
                        "BUILDER_API_KEY/SECRET/PASSPHRASE in .env. Auto-merge is OFF.",
                        self._merge_disabled_reason,
                    )
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
        amount: Union[float, str],
    ) -> MergeResult:
        """Execute a true on-chain gasless merge using the RelayClient SDK."""
        import asyncio
        from web3 import Web3

        async def _do_merge():
            current_amount = amount
            for attempt in range(2):
                try:
                    self.logger.info("⛽ Initiating GASLESS on-chain merge via Relayer SDK...")
                    
                    from py_builder_relayer_client.client import RelayClient, RelayerTxType
                    from py_builder_relayer_client.models import Transaction
                    from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds
                    
                    # Ensure amount is in base units (6 decimals for USDC collateral)
                    merge_arg = int(float(current_amount) * 1_000_000)

                    # Initialize the RelayClient using the actual v0.0.2 parameters
                    creds = BuilderApiKeyCreds(
                        key=self.bot.config.builder_api_key,
                        secret=self.bot.config.builder_api_secret,
                        passphrase=self.bot.config.builder_api_passphrase
                    )
                    builder_config = BuilderConfig(local_builder_creds=creds)

                    client = RelayClient(
                        relayer_url="https://relayer-v2.polymarket.com",
                        chain_id=137,
                        private_key=self.bot.config.private_key,
                        builder_config=builder_config,
                        relay_tx_type=RelayerTxType.PROXY
                    )
                    
                    # CTF Address for standard markets, NegRiskAdapter for neg risk
                    # Polymarket routes most new markets via NegRiskAdapter.
                    # We will attempt NegRiskAdapter first, as that is standard for newer pairs.
                    NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
                    
                    # Build the data payload for the merge transaction
                    tx_data = Web3().eth.contract(
                        address=NEG_RISK_ADAPTER,
                        abi=[{
                            "name": "mergePositions",
                            "type": "function",
                            "inputs": [
                                {"name": "_conditionId", "type": "bytes32"},
                                {"name": "_amount", "type": "uint256"}
                            ],
                            "outputs": []
                        }]
                    ).encode_abi(
                        abi_element_identifier="mergePositions", 
                        args=[Web3.to_bytes(hexstr=condition_id), merge_arg]
                    )

                    # RelayClient.execute() consumes Transaction dataclasses and
                    # reads t.to / t.data / t.value as attributes — a plain dict
                    # raises AttributeError, so build the model the SDK expects.
                    merge_tx = Transaction(
                        to=NEG_RISK_ADAPTER,
                        data=tx_data,
                        value="0",
                    )

                    # Execute via the Relayer SDK (synchronous call wrapping in asyncio)
                    self.logger.info("⏳ Merge submitted to Relayer. Waiting for confirmation...")
                    response = await asyncio.to_thread(client.execute, [merge_tx], "Merge pairs")

                    # The response already carries the submitted tx hash; capture it
                    # up front so we still have a reference even if polling times out.
                    submitted_hash = getattr(response, "transaction_hash", None) or getattr(response, "hash", None)

                    # Wait for on-chain confirmation. poll_until_state() returns the
                    # transaction dict on success or None on timeout/fail-state — so
                    # guard against None instead of calling .get() on it blindly.
                    result = await asyncio.to_thread(response.wait)
                    if not result:
                        self.logger.error(
                            "Merge submitted (hash=%s) but did not confirm before timeout.",
                            submitted_hash,
                        )
                        return MergeResult(
                            success=False, merged_shares=0, usdc_returned=0,
                            error=f"merge not confirmed before timeout (hash={submitted_hash})",
                        )

                    tx_hash = result.get("transactionHash") or result.get("hash") or submitted_hash or "success"
                    self.logger.info("✅ GASLESS MERGE MINED | tx_hash=%s", tx_hash)
                    
                    return MergeResult(
                        success=True, 
                        merged_shares=current_amount, 
                        usdc_returned=current_amount, 
                        tx_hash=tx_hash
                    )
                        
                except Exception as e:
                    error_msg = str(e)
                    self.logger.error("Unified SDK merge exception: %s", error_msg)
                    return MergeResult(success=False, merged_shares=0, usdc_returned=0, error=error_msg)
                    
            return MergeResult(success=False, merged_shares=0, usdc_returned=0, error="Max attempts reached")

        return asyncio.run(_do_merge())

    def log_session_summary(self) -> None:
        """Log total merges for this session."""
        self.logger.info(
            "📊 MERGE SESSION SUMMARY | total_merged=$%.2f | merge_count=%d",
            self._total_merged_usdc, self._total_merge_count
        )
