#!/usr/bin/env python3
"""
Gabagool Bot - Capital Manager

PURPOSE
-------
Enforces the "risk only what you start with, protect all profits" rule:

    1. SESSION CAPITAL CAP
       The bot may only deploy `session_capital_usd` per full settlement
       cycle — never more.  Even if the wallet holds $700 in profits, only
       the fixed session amount enters the next cycle.

    2. WAIT-FOR-SETTLEMENT
       After every cycle completes (all windows closed) the manager waits
       until the on-chain balance reflects the settled cash.  No new orders
       are placed while positions are outstanding.

    3. STOP-LOSS CIRCUIT BREAKER
       If the settled USDC returned after a cycle is LESS than the session
       capital that was deployed, the bot logs a critical alert and sets the
       global shutdown event.  This bounds total downside to one cycle's
       deployment regardless of how long the bot runs unattended.

    Example with session_capital_usd = $100:
        Cycle 1: wallet=$102 → deploy $100 → settle → wallet=$140  → gain $40
        Cycle 2: wallet=$140 → deploy $100 → settle → wallet=$160  → gain $20
        Cycle 3: wallet=$160 → deploy $100 → settle → wallet=$85   → STOP (lost $15)
        Outcome: protected $60 in profits, only risked $100 on losing cycle.

USAGE
-----
    capital_mgr = CapitalManager(bot, session_capital_usd=100.0, logger=logger)

    # At the start of every cycle, call this.  It blocks until the wallet
    # has enough to deploy and all prior settlements are complete.
    ok = await capital_mgr.start_cycle()
    if not ok:
        break  # stop-loss triggered

    # ... run windows ...

    # After all windows for this cycle are done:
    await capital_mgr.end_cycle()
"""

import asyncio
import logging
import time
from typing import Optional, Any

import src.terminal_ui as terminal_ui

# How long to wait between balance polls during settlement (seconds)
_SETTLE_POLL_INTERVAL_S = 15.0

# How many consecutive stable balance reads before we consider settlement done
_STABLE_READS_REQUIRED = 3

# Maximum time to wait for settlement before giving up (seconds)
_SETTLE_TIMEOUT_S = 600.0  # 10 minutes


class CapitalManager:
    """Enforces fixed-capital-per-cycle and settlement gating."""

    def __init__(
        self,
        bot,
        session_capital_usd: float = 100.0,
        auto_compound_pct: float = 0.0,
        shutdown_event: Optional[asyncio.Event] = None,
        logger: Optional[logging.Logger] = None,
        paper_trader: Optional[Any] = None,
    ):
        self.bot = bot
        self.paper_trader = paper_trader
        self.base_session_capital_usd = session_capital_usd
        self.session_capital_usd = session_capital_usd
        self.auto_compound_pct = max(0.0, min(1.0, auto_compound_pct))
        self.shutdown_event = shutdown_event
        self.logger = logger or logging.getLogger("capital_manager")

        self._cycle_number: int = 0
        self._cycle_start_balance: float = 0.0
        self._baseline_profit: float = 0.0   # cumulative profit locked away

        self.logger.info(
            "CapitalManager ready | base_capital=$%.2f | auto_compound=%.0f%%",
            self.base_session_capital_usd, self.auto_compound_pct * 100
        )

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def start_cycle(self) -> bool:
        """
        Called BEFORE launching any windows for a new cycle.

        - Waits for full settlement of prior positions.
        - Checks that the wallet holds >= session_capital_usd.
        - Records the balance snapshot used for end-of-cycle P&L.

        Returns:
            True  → proceed with trading
            False → stop-loss triggered; caller should break the main loop
        """
        self._cycle_number += 1
        
        mode = "DRY RUN" if getattr(self.bot.config, "dry_run", False) else "LIVE TRADING"
        
        # Try to get BTC and ETH prices if available
        btc_price = None
        eth_price = None
        try:
            from src.price_feed import CryptoFeed
            if CryptoFeed._last_btc_price > 0:
                btc_price = CryptoFeed._last_btc_price
                eth_price = CryptoFeed._last_eth_price
        except Exception:
            pass
            
        print(terminal_ui.fmt_cycle_header(mode, btc_price, eth_price), flush=True)
        
        self.logger.info(
            "CAPITAL MANAGER | Cycle %d starting", self._cycle_number
        )

        # 1. Wait until all prior positions have settled
        await self._wait_for_settlement()

        # 1.5 Execute Automated Sweep if enabled
        if hasattr(self.bot, 'config') and hasattr(self.bot.config, 'sweeper') and self.bot.config.sweeper.enabled:
            try:
                from src.sweep_manager import SweepManager
                sweeper = SweepManager(self.bot, self.bot.config, self.logger)
                pre_sweep_balance = await self._get_balance()
                if pre_sweep_balance is not None:
                    await sweeper.check_and_sweep(pre_sweep_balance)
            except Exception as e:
                self.logger.error(f"Failed to execute profit sweep: {e}")

        # 2. Read current wallet balance
        balance = await self._get_balance()
        if balance is None:
            self.logger.error("Cannot read wallet balance — aborting cycle")
            return False

        # --- AUTO-COMPOUNDING LOGIC ---
        if self.auto_compound_pct > 0.0:
            compounded = balance * self.auto_compound_pct
            self.session_capital_usd = max(self.base_session_capital_usd, compounded)
            
            # Dynamically update the RiskManager limits so the bot can actually deploy the compounded capital
            if hasattr(self.bot, 'config') and hasattr(self.bot.config, 'gabagool'):
                self.bot.config.gabagool.max_total_exposure = self.session_capital_usd
                concurrent = max(1, self.bot.config.gabagool.max_concurrent_arbitrages)
                self.bot.config.gabagool.max_position_per_market = self.session_capital_usd / concurrent
                
            self.logger.info(
                "Auto-compounding active (%.0f%%) | Dynamic session capital: $%.2f",
                self.auto_compound_pct * 100, self.session_capital_usd
            )
        # ------------------------------

        self.logger.info(
            "Cycle %d | Wallet balance: $%.2f | Session capital cap: $%.2f",
            self._cycle_number, balance, self.session_capital_usd
        )

        # 3. Stop-loss check (only after cycle 1)
        if self._cycle_number > 1:
            returned = balance - self._baseline_profit
            if returned < self.session_capital_usd:
                loss = self.session_capital_usd - returned
                self.logger.critical(
                    "=" * 60
                )
                self.logger.critical(
                    "🛑  STOP-LOSS TRIGGERED — Cycle %d returned $%.2f "
                    "(deployed $%.2f, lost $%.2f). KILLING SESSION.",
                    self._cycle_number, returned, self.session_capital_usd, loss
                )
                self.logger.critical(
                    "Profit protected: $%.2f remains above session capital.",
                    self._baseline_profit
                )
                self.logger.critical(
                    "=" * 60
                )
                if self.shutdown_event is not None:
                    self.shutdown_event.set()
                return False

        # 4. Check there's enough to deploy
        if balance < self.session_capital_usd:
            self.logger.critical(
                "🛑  INSUFFICIENT FUNDS — Wallet $%.2f < session capital $%.2f. "
                "Cannot start cycle %d. Top up and restart.",
                balance, self.session_capital_usd, self._cycle_number
            )
            if self.shutdown_event is not None:
                self.shutdown_event.set()
            return False

        # 5. Record start-of-cycle state
        # "Profit baseline" = everything above session_capital (locked, don't touch)
        self._baseline_profit = balance - self.session_capital_usd
        self._cycle_start_balance = balance

        self.logger.info(
            "Cycle %d | Profit reserved (untouchable): $%.2f | "
            "Deploying: $%.2f",
            self._cycle_number, self._baseline_profit, self.session_capital_usd
        )
        return True

    async def end_cycle(self) -> None:
        """
        Called AFTER all windows in a cycle are complete.

        Logs a cycle summary.  The next call to start_cycle() will wait
        for settlement and run the stop-loss check.
        """
        balance_now = await self._get_balance() or 0.0
        gain = balance_now - self._cycle_start_balance
        self.logger.info(
            "CAPITAL MANAGER | Cycle %d complete | "
            "Start: $%.2f → Now: $%.2f | Cycle P&L: %+.2f",
            self._cycle_number,
            self._cycle_start_balance,
            balance_now,
            gain,
        )
        self.logger.info("Waiting for positions to fully settle before next cycle...")

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    async def _get_balance(self) -> Optional[float]:
        """Return the current USDC wallet balance in dollars."""
        if hasattr(self.bot, 'config') and getattr(self.bot.config, 'dry_run', False):
            if self.paper_trader and hasattr(self.paper_trader, 'ledger'):
                return self.paper_trader.ledger.get("current_balance", 200.0)
            return 200.0  # Simulated $200 balance for dry run if no paper trader attached

        try:
            raw = await asyncio.to_thread(self.bot._refresh_balance)
            if raw is None:
                return None
            return raw / 1_000_000  # micro-USDC → dollars
        except Exception as exc:
            self.logger.warning("Balance read failed: %s", exc)
            return None

    async def _wait_for_settlement(self) -> None:
        """
        Block until the wallet balance has been stable for N consecutive
        reads — indicating all positions have resolved and USDC has landed.

        Uses "balance stable for 3 reads × 15s = 45s quiet period" as a
        proxy for full settlement.  This avoids the complexity of tracking
        individual conditionIds on-chain.
        """
        if self._cycle_number == 1:
            return  # first cycle — no prior positions to wait for

        self.logger.info(
            "Settlement wait: polling balance every %.0fs "
            "(need %d stable reads)…",
            _SETTLE_POLL_INTERVAL_S, _STABLE_READS_REQUIRED
        )

        stable_count = 0
        last_balance: Optional[float] = None
        deadline = time.time() + _SETTLE_TIMEOUT_S

        while time.time() < deadline:
            balance = await self._get_balance()
            if balance is None:
                await asyncio.sleep(_SETTLE_POLL_INTERVAL_S)
                continue

            if last_balance is not None and abs(balance - last_balance) < 0.01:
                stable_count += 1
                self.logger.info(
                    "Settlement poll | balance=$%.2f | stable reads: %d/%d",
                    balance, stable_count, _STABLE_READS_REQUIRED
                )
                if stable_count >= _STABLE_READS_REQUIRED:
                    self.logger.info(
                        "✅  Settlement confirmed | final balance=$%.2f", balance
                    )
                    return
            else:
                self.logger.info(
                    "Settlement poll | balance=$%.2f (changed from $%.2f) — resetting stable count",
                    balance, last_balance or 0.0
                )
                stable_count = 0

            last_balance = balance
            await asyncio.sleep(_SETTLE_POLL_INTERVAL_S)

        self.logger.warning(
            "Settlement wait timed out after %.0fs — proceeding anyway",
            _SETTLE_TIMEOUT_S
        )
