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
# Increased to 50.0 to batch merges and save on gas fees, replicating Gabagool22's efficiency
MIN_MERGE_SHARES = 50.0

# How often to attempt merges per window (seconds) — don't spam the chain
MERGE_COOLDOWN_S = 5.0

# Sniper inventory reserve: always keep at least this many balanced shares on each
# side AFTER merging so the sniper has opposing inventory to fire against on the
# next spike.  Without this, an aggressive merge drains one side to 0 and the
# sniper immediately aborts with "insufficient_opposing_shares".
SNIPER_INVENTORY_RESERVE = 15.0


@dataclass
class MergeResult:
    success: bool
    merged_shares: float
    usdc_returned: float   # 1 share pair = $1.00 USDC
    error: Optional[str] = None


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
        # Keep SNIPER_INVENTORY_RESERVE balanced shares on each side so the
        # sniper can still fire opposing-side coverage after a merge.  Only
        # merge the surplus above that reserve; force=True (HOLD phase at
        # window close) bypasses the reserve since no more snipes will fire.
        if force:
            mergeable = min(up_shares, down_shares)
        else:
            mergeable = max(0.0, min(up_shares, down_shares) - SNIPER_INVENTORY_RESERVE)

        if mergeable < MIN_MERGE_SHARES and not force:
            return MergeResult(success=False, merged_shares=0, usdc_returned=0,
                               error=f"not enough to merge ({mergeable:.2f} < {MIN_MERGE_SHARES})")

        self._last_merge_at = now
        condition_id = market.id

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

        # Live merge via CLOB client
        try:
            result = await asyncio.to_thread(
                self._execute_merge_via_clob,
                condition_id,
                mergeable,
                market.yes_token_id,
                market.no_token_id,
            )

            if result.success:
                # Deduct merged shares from the summary tracking
                summary.up_shares -= result.merged_shares
                summary.down_shares -= result.merged_shares
                summary.merged_usdc += result.usdc_returned
                self._total_merged_usdc += result.usdc_returned
                self._total_merge_count += 1
                print(terminal_ui.fmt_merge(summary.market_id, result.merged_shares, result.usdc_returned), flush=True)
            else:
                self.logger.warning("MERGE FAILED: %s", result.error)

            return result

        except Exception as e:
            self.logger.error("Merge exception: %s", e)
            return MergeResult(success=False, merged_shares=0, usdc_returned=0,
                               error=str(e))

    def _execute_merge_via_clob(
        self,
        condition_id: str,
        amount: float,
        yes_token_id: str,
        no_token_id: str,
    ) -> MergeResult:
        """
        Execute merge via the py-clob-client-v2 SDK.

        The SDK's merge_positions() call handles neg-risk routing automatically
        — no need for us to distinguish neg-risk vs standard markets.

        The CLOB endpoint POST /merge takes:
          {
            "conditionId": "0x...",
            "amount": <shares as float>,
            "collateralToken": "0x2791Bca1...",   (USDC on Polygon)
          }

        On success, the server burns the tokens and credits USDC to our wallet.
        The client returns the transaction hash or a success flag.
        """
        try:
            clob = self.bot.clob

            # Try the SDK merge method if it exists (py-clob-client-v2 >= 0.5)
            if hasattr(clob, 'merge_positions'):
                resp = clob.merge_positions(condition_id, amount)
                if resp and (resp.get('success') or resp.get('transactionHash')):
                    return MergeResult(
                        success=True,
                        merged_shares=amount,
                        usdc_returned=amount,  # 1 share pair = $1.00 USDC
                    )
                else:
                    return MergeResult(
                        success=False,
                        merged_shares=0,
                        usdc_returned=0,
                        error=f"SDK merge returned: {resp}"
                    )

            # Fallback: call the CLOB REST endpoint directly via the session
            elif hasattr(clob, '_post') or hasattr(clob, 'session'):
                return self._merge_via_rest(clob, condition_id, amount)

            else:
                return MergeResult(
                    success=False, merged_shares=0, usdc_returned=0,
                    error="CLOB client has no merge_positions method — upgrade py-clob-client-v2"
                )

        except Exception as e:
            return MergeResult(
                success=False, merged_shares=0, usdc_returned=0, error=str(e)
            )

    def _merge_via_rest(self, clob: Any, condition_id: str, amount: float) -> MergeResult:
        """
        Direct REST call to POST /merge on the CLOB API.
        Used as a fallback if the SDK doesn't expose merge_positions().
        """
        import requests

        USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

        host = getattr(clob, 'host', 'https://clob.polymarket.com')
        url = f"{host}/merge"

        # Pull auth headers from the CLOB client's session/creds
        headers = {"Content-Type": "application/json"}
        if hasattr(clob, 'get_auth_headers'):
            headers.update(clob.get_auth_headers())
        elif hasattr(clob, 'creds') and clob.creds:
            headers["POLY-API-KEY"] = clob.creds.api_key
            headers["POLY-API-SECRET"] = clob.creds.api_secret
            headers["POLY-API-PASSPHRASE"] = clob.creds.api_passphrase

        payload = {
            "conditionId": condition_id,
            "amount": str(int(amount * 1_000_000)),  # micro-USDC
            "collateralToken": USDC_POLYGON,
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=10)

        if resp.status_code in (200, 201):
            data = resp.json() if resp.content else {}
            return MergeResult(
                success=True,
                merged_shares=amount,
                usdc_returned=amount,
            )
        else:
            return MergeResult(
                success=False,
                merged_shares=0,
                usdc_returned=0,
                error=f"REST merge failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )

    def log_session_summary(self) -> None:
        """Log total merges for this session."""
        self.logger.info(
            "📊 MERGE SESSION SUMMARY | total_merged=$%.2f | merge_count=%d",
            self._total_merged_usdc, self._total_merge_count
        )
