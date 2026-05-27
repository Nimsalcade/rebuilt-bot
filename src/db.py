#!/usr/bin/env python3
"""
Gabagool Bot - Database (SQLite Persistence)

Purpose:
    SQLite persistence for positions, trades, and settlements.
    Enables bot restart recovery and historical analysis.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Dependencies:
    - sqlite3
    - logging
    - datetime

Usage:
    from src.db import TradingDatabase

    db = TradingDatabase('gabagool.db')
    db.save_position(position)
    db.save_trade(market_id, 'YES', shares, price, cost)

    # On restart
    positions = db.load_active_positions()

Notes:
    - Thread-safe (check_same_thread=False)
    - Creates tables automatically on init
    - Provides recovery on bot restart
"""

import sqlite3
import logging
from datetime import datetime
from typing import List, Tuple, Optional, Any
from pathlib import Path


class TradingDatabase:
    """
    SQLite database for trading persistence.

    Tables:
        - positions: Arbitrage positions (YES/NO pairs)
        - trades: Individual trade records
        - settlements: Resolved position records
    """

    def __init__(self, db_path: str = "gabagool.db"):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.logger = logging.getLogger("database")

        # Create connection (thread-safe)
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row  # Enable dict-like access

        # Initialize tables
        self.create_tables()
        self.logger.info("Database initialized: %s", self.db_path)

    def create_tables(self) -> None:
        """Create database schema if not exists."""
        # Positions table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT UNIQUE NOT NULL,
                yes_token_id TEXT,
                no_token_id TEXT,
                yes_shares REAL DEFAULT 0,
                yes_avg_cost REAL DEFAULT 0,
                yes_total_cost REAL DEFAULT 0,
                no_shares REAL DEFAULT 0,
                no_avg_cost REAL DEFAULT 0,
                no_total_cost REAL DEFAULT 0,
                opened_at TIMESTAMP,
                resolved INTEGER DEFAULT 0,
                profit REAL DEFAULT 0,
                holding_time_limit INTEGER DEFAULT 1800,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Trades table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                shares REAL NOT NULL,
                price REAL NOT NULL,
                cost REAL NOT NULL,
                order_id TEXT,
                status TEXT DEFAULT 'filled',
                timestamp TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Settlements table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                winning_side TEXT,
                pairs_settled REAL,
                profit REAL,
                gas_used INTEGER,
                tx_hash TEXT,
                settled_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_market
            ON positions(market_id)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_resolved
            ON positions(resolved)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_market
            ON trades(market_id)
        """)

        self.conn.commit()
        self.logger.debug("Database tables created/verified")

    def save_position(self, position: Any) -> bool:
        """
        Save or update an arbitrage position.

        Args:
            position: ArbitragePosition object

        Returns:
            True if saved successfully
        """
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO positions (
                    market_id, yes_token_id, no_token_id,
                    yes_shares, yes_avg_cost, yes_total_cost,
                    no_shares, no_avg_cost, no_total_cost,
                    opened_at, resolved, profit, holding_time_limit,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position.market_id,
                position.yes_token_id,
                position.no_token_id,
                position.yes_shares,
                position.yes_avg_cost,
                position.yes_total_cost,
                position.no_shares,
                position.no_avg_cost,
                position.no_total_cost,
                position.opened_at,
                1 if position.resolved else 0,
                getattr(position, 'profit', 0),
                position.holding_time_limit,
                datetime.now()
            ))
            self.conn.commit()
            return True
        except Exception as e:
            self.logger.error("Failed to save position: %s", e)
            return False

    def save_trade(
        self,
        market_id: str,
        side: str,
        shares: float,
        price: float,
        cost: float,
        order_id: str = None,
        status: str = "filled"
    ) -> bool:
        """
        Save individual trade record.

        Args:
            market_id: Market identifier
            side: 'YES' or 'NO'
            shares: Number of shares
            price: Price per share
            cost: Total cost
            order_id: Exchange order ID (optional)
            status: Trade status

        Returns:
            True if saved successfully
        """
        try:
            self.conn.execute("""
                INSERT INTO trades (
                    market_id, side, shares, price, cost,
                    order_id, status, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id, side, shares, price, cost,
                order_id, status, datetime.now()
            ))
            self.conn.commit()
            return True
        except Exception as e:
            self.logger.error("Failed to save trade: %s", e)
            return False

    def save_settlement(
        self,
        market_id: str,
        winning_side: str,
        pairs_settled: float,
        profit: float,
        gas_used: int = 0,
        tx_hash: str = None
    ) -> bool:
        """
        Save settlement record.

        Args:
            market_id: Market identifier
            winning_side: 'YES' or 'NO'
            pairs_settled: Number of pairs settled
            profit: Realized profit
            gas_used: Gas used for transaction
            tx_hash: Transaction hash

        Returns:
            True if saved successfully
        """
        try:
            self.conn.execute("""
                INSERT INTO settlements (
                    market_id, winning_side, pairs_settled, profit,
                    gas_used, tx_hash, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id, winning_side, pairs_settled, profit,
                gas_used, tx_hash, datetime.now()
            ))
            self.conn.commit()
            return True
        except Exception as e:
            self.logger.error("Failed to save settlement: %s", e)
            return False

    def load_active_positions(self) -> List[sqlite3.Row]:
        """
        Load all unresolved positions.

        Returns:
            List of position rows
        """
        cursor = self.conn.execute("""
            SELECT * FROM positions WHERE resolved = 0
        """)
        return cursor.fetchall()

    def get_position(self, market_id: str) -> Optional[sqlite3.Row]:
        """
        Get position by market ID.

        Args:
            market_id: Market identifier

        Returns:
            Position row or None
        """
        cursor = self.conn.execute("""
            SELECT * FROM positions WHERE market_id = ?
        """, (market_id,))
        return cursor.fetchone()

    def get_trade_history(self, limit: int = 100) -> List[sqlite3.Row]:
        """
        Get recent trade history.

        Args:
            limit: Max records to return

        Returns:
            List of trade rows
        """
        cursor = self.conn.execute("""
            SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?
        """, (limit,))
        return cursor.fetchall()

    def get_settlement_history(self, limit: int = 100) -> List[sqlite3.Row]:
        """
        Get recent settlement history.

        Args:
            limit: Max records to return

        Returns:
            List of settlement rows
        """
        cursor = self.conn.execute("""
            SELECT * FROM settlements ORDER BY settled_at DESC LIMIT ?
        """, (limit,))
        return cursor.fetchall()

    def get_performance_stats(self) -> dict:
        """
        Calculate performance statistics from database.

        Returns:
            Dict with performance metrics
        """
        # Position stats
        pos_cursor = self.conn.execute("""
            SELECT
                COUNT(*) as total_positions,
                SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved_positions,
                SUM(profit) as total_profit,
                AVG(CASE WHEN profit > 0 THEN profit ELSE NULL END) as avg_profit
            FROM positions
        """)
        pos_stats = pos_cursor.fetchone()

        # Trade stats
        trade_cursor = self.conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(cost) as total_volume
            FROM trades
        """)
        trade_stats = trade_cursor.fetchone()

        return {
            "total_positions": pos_stats["total_positions"] or 0,
            "resolved_positions": pos_stats["resolved_positions"] or 0,
            "total_profit": pos_stats["total_profit"] or 0,
            "avg_profit": pos_stats["avg_profit"] or 0,
            "total_trades": trade_stats["total_trades"] or 0,
            "total_volume": trade_stats["total_volume"] or 0
        }

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()
        self.logger.info("Database connection closed")
