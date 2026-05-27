#!/usr/bin/env python3
"""
Gabagool Bot - Paper Trader (Simulated Settlement)

Purpose:
    Maintains a simulated wallet and resolves completed dry-run windows.
    Periodically polls Polymarket for market resolution, determines the winner,
    and calculates accurate simulated PnL to track strategy performance
    without risking real capital.

Author: AI-Generated
Created: 2026-05-03
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime

from src.maker_loop import WindowFillSummary
from src.gamma_client import GammaClient
import src.terminal_ui as terminal_ui


class PaperTrader:
    """
    Paper trading engine that tracks simulated PnL.
    
    1. Receives completed WindowFillSummary objects from WindowManager
    2. Queues them for settlement polling
    3. Queries Polymarket API to see which side won
    4. Updates a local JSON ledger with the final realized PnL
    """

    def __init__(self, ledger_path: str, starting_balance: float = 200.0):
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.gamma = GammaClient()
        self.logger = logging.getLogger("paper_trader")
        
        # In-memory settlement queue
        self.pending_windows: Dict[str, WindowFillSummary] = {}
        
        # Load or initialize ledger
        self.ledger = self._load_ledger(starting_balance)
        self.running = False
        
        # Session stats for banner
        self.session_start_time = datetime.now().strftime("%H:%M")
        self.session_invested = 0.0
        self.session_merged = 0.0
        self.session_redeemed = 0.0
        self.session_pnl = 0.0

    def _load_ledger(self, starting_balance: float) -> Dict[str, Any]:
        """Load paper trading ledger from disk or initialize it."""
        if self.ledger_path.exists():
            try:
                with open(self.ledger_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                self.logger.error("Failed to load paper ledger: %s", e)
                
        return {
            "starting_balance": starting_balance,
            "current_balance": starting_balance,
            "total_trades": 0,
            "winning_trades": 0,
            "total_pnl": 0.0,
            "history": []
        }

    def _save_ledger(self) -> None:
        """Save paper trading ledger to disk."""
        try:
            with open(self.ledger_path, "w") as f:
                json.dump(self.ledger, f, indent=2)
        except Exception as e:
            self.logger.error("Failed to save paper ledger: %s", e)

    def queue_for_settlement(self, summary: WindowFillSummary) -> None:
        """Add a completed window to the settlement queue."""
        # Only queue if we actually filled shares
        if summary.up_shares > 0 or summary.down_shares > 0:
            self.pending_windows[summary.market_id] = summary
            self.logger.info("Queued %s for paper settlement", summary.market_id[:16])

    async def run_settlement_loop(self) -> None:
        """Background loop to poll for market resolution."""
        self.running = True
        self.logger.info("PaperTrader settlement loop started")
        
        while self.running:
            try:
                if self.pending_windows:
                    await self._check_settlements()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Paper settlement error: %s", e)
                
            # Check every 60 seconds
            await asyncio.sleep(60)

    async def _check_settlements(self) -> None:
        """Check Polymarket API for resolution of pending windows."""
        resolved_ids = []
        
        for market_id, summary in self.pending_windows.items():
            try:
                # Query Gamma API directly for this market
                url = f"{self.gamma.host}/markets/{market_id}"
                resp = await asyncio.to_thread(self.gamma.session.get, url, timeout=10)
                
                if resp.status_code != 200:
                    continue
                    
                data = resp.json()
                
                # Check if market is closed/resolved
                # Polymarket outcomePrices go to ~1.0 for the winner and ~0.0 for loser
                if data.get("closed") or data.get("resolved"):
                    outcomes = data.get("outcomes", '["Up", "Down"]')
                    prices = data.get("outcomePrices", '["0.5", "0.5"]')
                    
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)
                    if isinstance(prices, str):
                        prices = json.loads(prices)
                        
                    # Find the winning index (price > 0.9)
                    winner = None
                    for i, price in enumerate(prices):
                        if float(price) > 0.9:
                            winner = str(outcomes[i]).upper()
                            break
                            
                    if winner:
                        self._process_settlement(summary, winner)
                        resolved_ids.append(market_id)
                    else:
                        self.logger.debug("Market %s closed but no clear winner yet", market_id[:16])
                        
            except Exception as e:
                self.logger.debug("Error checking %s: %s", market_id[:16], e)
                
                
        # Remove resolved windows from queue
        for mid in resolved_ids:
            del self.pending_windows[mid]
            
        if resolved_ids:
            # Print session summary banner
            win_rate = self.ledger["winning_trades"] / max(1, self.ledger["total_trades"])
            roi = self.session_pnl / max(1.0, self.session_invested)
            
            # Since the ledger runs across multiple sessions potentially, let's use the local session stats
            print(terminal_ui.fmt_session_summary(
                start_time=self.session_start_time,
                end_time=datetime.now().strftime("%H:%M"),
                total_windows=self.ledger["total_trades"],  # Note: this is total lifetime trades, let's use it for now or just the count of resolved
                wins=self.ledger["winning_trades"],
                losses=self.ledger["total_trades"] - self.ledger["winning_trades"],
                win_rate=win_rate,
                invested=self.session_invested,
                merged=self.session_merged,
                redeemed=self.session_redeemed,
                net_pnl=self.session_pnl,
                roi=roi,
                balance=self.ledger["current_balance"],
                previous_balance=self.ledger["starting_balance"]
            ), flush=True)

    def _process_settlement(self, summary: WindowFillSummary, winner: str) -> None:
        """Calculate PnL for a resolved window and update ledger."""
        revenue = summary.merged_usdc
        redeemed = 0.0
        
        # 1 share of the winning side = $1.00 payout
        if winner == "UP":
            redeemed = summary.up_shares * 1.00
        elif winner == "DOWN" or winner == "NO":
            redeemed = summary.down_shares * 1.00
            
        revenue += redeemed
            
        # PnL = Revenue - Total Cost
        pnl = revenue - summary.total_invested
        
        # Was our signal right?
        signal_correct = (summary.lean_direction == winner) if summary.lean_direction else None
        
        # Update summary
        summary.winner = winner
        summary.pnl = pnl
        
        # Update session stats
        self.session_invested += summary.total_invested
        self.session_merged += summary.merged_usdc
        self.session_redeemed += redeemed
        self.session_pnl += pnl
        
        # Update ledger
        self.ledger["total_trades"] += 1
        if pnl > 0:
            self.ledger["winning_trades"] += 1
            
        self.ledger["total_pnl"] += pnl
        self.ledger["current_balance"] += pnl
        
        # Save history record
        record = {
            "timestamp": time.time(),
            "market_id": summary.market_id,
            "lean": summary.lean_direction,
            "winner": winner,
            "signal_correct": signal_correct,
            "invested": summary.total_invested,
            "merged": summary.merged_usdc,
            "redeemed": redeemed,
            "pnl": pnl,
            "up_shares": summary.up_shares,
            "down_shares": summary.down_shares
        }
        self.ledger["history"].append(record)
        self._save_ledger()

    def stop(self) -> None:
        self.running = False
