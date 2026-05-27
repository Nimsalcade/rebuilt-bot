#!/usr/bin/env python3
"""
Rust-Powered Data Collection Pipeline

Purpose:
    High-speed continuous data collection for Polymarket 15-minute markets.
    Uses Rust backend for storage and processing, Python for API access.
    Feeds data to parallel paper trader array for strategy evaluation.

Author: AI-Generated
Created: 2026-01-27
Modified: 2026-01-27

Architecture:
    Python (API Layer) -> Rust (Storage & Processing) -> SQLite (Persistence)
                                    |
                                    v
                        Paper Trader Array (Evaluation)

Target Performance:
    - 100+ updates/second throughput
    - <5ms per update processing (Rust)
    - Continuous 24/7 operation

Usage:
    python scripts/data_collector_rust.py --traders 10

Output:
    - data/gabagool_data.db (SQLite - all market data)
    - Console: Real-time statistics and opportunities

Dependencies:
    - gabagool_rust (maturin develop)
    - py_clob_client
    - src.gamma_client
"""

import sys
import time
import json
import argparse
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Python API clients
from src.gamma_client import GammaClient
from py_clob_client.client import ClobClient

# Rust backend
try:
    import gabagool_rust
    RUST_AVAILABLE = True
    print(f"Rust backend: {gabagool_rust.health_check()}")
except ImportError as e:
    RUST_AVAILABLE = False
    print(f"ERROR: Rust module not available ({e})")
    print("Run: cd rust && maturin develop")
    sys.exit(1)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class CollectorConfig:
    """Data collector configuration."""
    coins: List[str] = None
    scan_interval_ms: int = 100  # Fast scanning for data collection
    book_depth: int = 5
    db_path: str = "data/gabagool_data.db"
    min_margin: float = 0.005
    store_orderbook: bool = False
    max_workers: int = 8

    # Paper trader array
    num_traders: int = 10

    def __post_init__(self):
        if self.coins is None:
            self.coins = ["BTC", "ETH", "SOL", "XRP"]


@dataclass
class PaperTraderConfig:
    """Configuration for a single paper trader."""
    trader_id: str
    yes_threshold: float  # Max YES ask price to buy
    no_threshold: float   # Max NO ask price to buy
    profit_threshold: float  # Min profit margin
    max_trade_size: float = 100.0
    gas_cost: float = 0.003


# =============================================================================
# LOGGING
# =============================================================================

def setup_logging() -> logging.Logger:
    """Setup logging."""
    logger = logging.getLogger("data_collector")
    logger.setLevel(logging.INFO)
    logger.handlers = []

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', '%H:%M:%S'))
    logger.addHandler(console)

    Path("logs").mkdir(exist_ok=True)
    file_handler = logging.FileHandler("logs/data_collector.log", mode='a')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
    logger.addHandler(file_handler)

    return logger


# =============================================================================
# PAPER TRADER (Lightweight for Array)
# =============================================================================

class LightweightPaperTrader:
    """
    Lightweight paper trader for strategy array.

    Each trader has different parameters and tracks its own performance.
    Uses Rust strategy detection for speed.
    """

    def __init__(self, config: PaperTraderConfig):
        self.config = config
        self.trader_id = config.trader_id

        # Rust strategy component
        self.strategy = gabagool_rust.GabagoolStrategy(
            min_margin=config.profit_threshold
        )

        # Rust position tracker
        self.tracker = gabagool_rust.PositionTracker()

        # Simple stats
        self.trades = 0
        self.wins = 0
        self.total_pnl = 0.0
        self.opportunities_seen = 0
        self.last_trade_time: Dict[str, float] = {}

        # Open positions (simplified)
        self.open_positions: Dict[str, dict] = {}

    def evaluate(self, market_id: str, coin: str,
                 yes_ask: float, no_ask: float,
                 yes_size: float, no_size: float,
                 window_end_ts: int, current_ts: int) -> Optional[dict]:
        """
        Evaluate market data and potentially open trade.

        Returns trade info if opened, None otherwise.
        """
        # Check thresholds
        if yes_ask > self.config.yes_threshold:
            return None
        if no_ask > self.config.no_threshold:
            return None

        # Use Rust strategy detection
        opp = self.strategy.detect_arbitrage(
            market_id, coin,
            yes_ask, no_ask,
            yes_size, no_size
        )

        if not opp:
            return None

        self.opportunities_seen += 1

        # Check if we already have a position in this market
        if market_id in self.open_positions:
            return None

        # Check trade cooldown (max 1 trade per market per minute)
        last_trade = self.last_trade_time.get(market_id, 0)
        if time.time() - last_trade < 60:
            return None

        # Calculate trade size (liquidity-limited)
        combined_ask = yes_ask + no_ask
        min_liquidity = min(yes_size * yes_ask, no_size * no_ask)
        trade_size = min(self.config.max_trade_size, min_liquidity * 2)

        if trade_size < 1.0:
            return None

        # Open position
        tokens = trade_size / combined_ask
        entry_cost = trade_size
        gas_cost = self.config.gas_cost * 2

        position = {
            'market_id': market_id,
            'coin': coin,
            'entry_ts': time.time(),
            'window_end_ts': window_end_ts,
            'entry_combined_ask': combined_ask,
            'entry_margin': float(opp['gross_margin']),
            'trade_size': trade_size,
            'tokens': tokens,
            'entry_cost': entry_cost,
            'gas_cost': gas_cost,
        }

        self.open_positions[market_id] = position
        self.last_trade_time[market_id] = time.time()

        # Track in Rust
        self.tracker.add_yes_position(market_id, tokens, tokens * yes_ask)
        self.tracker.add_no_position(market_id, tokens, tokens * no_ask)

        return position

    def check_closures(self, current_ts: int) -> List[dict]:
        """Check for position closures (market resolution)."""
        closed = []

        for market_id, pos in list(self.open_positions.items()):
            if current_ts > pos['window_end_ts']:
                # Market resolved - close position
                payout = pos['tokens']  # $1 per token
                gross_pnl = payout - pos['entry_cost']
                net_pnl = gross_pnl - pos['gas_cost']

                pos['exit_ts'] = time.time()
                pos['payout'] = payout
                pos['gross_pnl'] = gross_pnl
                pos['net_pnl'] = net_pnl

                # Update stats
                self.trades += 1
                self.total_pnl += net_pnl
                if net_pnl > 0:
                    self.wins += 1

                del self.open_positions[market_id]
                closed.append(pos)

        return closed

    def get_stats(self) -> dict:
        """Get trader statistics."""
        win_rate = (self.wins / self.trades * 100) if self.trades > 0 else 0
        return {
            'trader_id': self.trader_id,
            'yes_threshold': self.config.yes_threshold,
            'no_threshold': self.config.no_threshold,
            'profit_threshold': self.config.profit_threshold,
            'trades': self.trades,
            'wins': self.wins,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'opportunities_seen': self.opportunities_seen,
            'open_positions': len(self.open_positions),
        }


# =============================================================================
# PAPER TRADER ARRAY
# =============================================================================

def create_trader_array(num_traders: int) -> List[LightweightPaperTrader]:
    """
    Create array of paper traders with different parameters.

    Creates a grid of:
    - YES thresholds: 0.47 - 0.50
    - NO thresholds: 0.47 - 0.50
    - Profit thresholds: 0.01 - 0.03
    """
    traders = []

    # Parameter grid
    yes_thresholds = [0.47, 0.48, 0.49, 0.50]
    no_thresholds = [0.47, 0.48, 0.49, 0.50]
    profit_thresholds = [0.01, 0.015, 0.02, 0.025]

    trader_id = 0
    for yes_t in yes_thresholds:
        for no_t in no_thresholds:
            for profit_t in profit_thresholds:
                if trader_id >= num_traders:
                    break

                config = PaperTraderConfig(
                    trader_id=f"T{trader_id:03d}",
                    yes_threshold=yes_t,
                    no_threshold=no_t,
                    profit_threshold=profit_t,
                )
                traders.append(LightweightPaperTrader(config))
                trader_id += 1
            if trader_id >= num_traders:
                break
        if trader_id >= num_traders:
            break

    return traders


# =============================================================================
# MARKET SCANNER
# =============================================================================

class FastMarketScanner:
    """Fast market scanner for data collection."""

    def __init__(self, config: CollectorConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.gamma = GammaClient()
        self.clob = ClobClient(host="https://clob.polymarket.com")
        self.executor = ThreadPoolExecutor(max_workers=config.max_workers)
        self.token_cache: Dict[str, Dict] = {}
        self.cache_window_ts: int = 0

    def _get_window_timestamp(self) -> int:
        now = datetime.now(timezone.utc)
        minute = (now.minute // 15) * 15
        window = now.replace(minute=minute, second=0, microsecond=0)
        return int(window.timestamp())

    def _refresh_token_cache(self) -> None:
        current_window = self._get_window_timestamp()
        if current_window == self.cache_window_ts and self.token_cache:
            return

        self.token_cache = {}
        for coin in self.config.coins:
            try:
                market = self.gamma.get_current_15m_market(coin)
                if market:
                    token_ids = self.gamma.parse_token_ids(market)
                    self.token_cache[coin] = {
                        "slug": market.get("slug", ""),
                        "end_date": market.get("endDate", ""),
                        "up_token": token_ids.get("up") or token_ids.get("yes"),
                        "down_token": token_ids.get("down") or token_ids.get("no")
                    }
            except Exception as e:
                self.logger.warning(f"Failed to get market for {coin}: {e}")
        self.cache_window_ts = current_window

    def _fetch_order_book(self, token_id: str) -> Optional[dict]:
        try:
            raw = self.clob.get_order_book(token_id)
            if not raw:
                return None

            # Parse bids/asks
            bids = []
            asks = []

            if hasattr(raw, 'bids') and raw.bids:
                bids = sorted(
                    [{'price': float(b.price), 'size': float(b.size)} for b in raw.bids],
                    key=lambda x: x['price'],
                    reverse=True
                )[:self.config.book_depth]

            if hasattr(raw, 'asks') and raw.asks:
                asks = sorted(
                    [{'price': float(a.price), 'size': float(a.size)} for a in raw.asks],
                    key=lambda x: x['price']
                )[:self.config.book_depth]

            best_bid = bids[0]['price'] if bids else 0.0
            best_ask = asks[0]['price'] if asks else 1.0
            best_bid_size = bids[0]['size'] if bids else 0.0
            best_ask_size = asks[0]['size'] if asks else 0.0

            return {
                'best_bid': best_bid,
                'best_ask': best_ask,
                'best_bid_size': best_bid_size,
                'best_ask_size': best_ask_size,
                'bids': bids,
                'asks': asks,
            }
        except Exception:
            return None

    def scan_all_markets(self) -> List[dict]:
        """Scan all markets and return data for processing."""
        self._refresh_token_cache()
        now = datetime.now(timezone.utc)
        results = []

        fetch_tasks = []
        for coin, cache in self.token_cache.items():
            if cache.get("up_token") and cache.get("down_token"):
                fetch_tasks.append((coin, "up", cache["up_token"]))
                fetch_tasks.append((coin, "down", cache["down_token"]))

        books: Dict[str, Dict[str, dict]] = {}
        futures = {}
        for coin, side, token_id in fetch_tasks:
            future = self.executor.submit(self._fetch_order_book, token_id)
            futures[future] = (coin, side, token_id)

        for future in as_completed(futures):
            coin, side, token_id = futures[future]
            try:
                book = future.result()
                if book:
                    if coin not in books:
                        books[coin] = {}
                    books[coin][side] = book
                    books[coin][f"{side}_token"] = token_id
            except Exception:
                pass

        for coin in self.config.coins:
            if coin not in books or "up" not in books[coin] or "down" not in books[coin]:
                continue

            cache = self.token_cache.get(coin, {})
            up_book = books[coin]["up"]
            down_book = books[coin]["down"]

            end_str = cache.get("end_date", "")
            remaining_secs = 0.0
            window_end_ts = 0
            if end_str:
                try:
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    remaining_secs = (end_dt - now).total_seconds()
                    window_end_ts = int(end_dt.timestamp())
                except:
                    pass

            results.append({
                "coin": coin,
                "slug": cache.get("slug", ""),
                "yes_token_id": books[coin].get("up_token", ""),
                "no_token_id": books[coin].get("down_token", ""),
                "yes_ask": up_book['best_ask'],
                "yes_ask_size": up_book['best_ask_size'],
                "yes_bid": up_book['best_bid'],
                "yes_bid_size": up_book['best_bid_size'],
                "no_ask": down_book['best_ask'],
                "no_ask_size": down_book['best_ask_size'],
                "no_bid": down_book['best_bid'],
                "no_bid_size": down_book['best_bid_size'],
                "window_end_ts": window_end_ts,
                "seconds_remaining": remaining_secs,
                "orderbook_json": json.dumps({
                    "up": up_book,
                    "down": down_book
                }) if self.config.store_orderbook else None,
            })

        return results

    def shutdown(self):
        self.executor.shutdown(wait=False)


# =============================================================================
# MAIN COLLECTOR LOOP
# =============================================================================

def run_collector(config: CollectorConfig):
    """Run the data collector with paper trader array."""
    logger = setup_logging()

    # Initialize components
    scanner = FastMarketScanner(config, logger)
    collector = gabagool_rust.DataCollector(
        config.db_path,
        config.min_margin,
        config.store_orderbook
    )
    collector.start()

    # Create paper trader array
    traders = create_trader_array(config.num_traders)

    logger.info("=" * 70)
    logger.info("  RUST DATA COLLECTOR - Strategic Research Pipeline")
    logger.info("=" * 70)
    logger.info(f"Rust: {gabagool_rust.health_check()}")
    logger.info(f"Coins: {', '.join(config.coins)}")
    logger.info(f"Scan interval: {config.scan_interval_ms}ms")
    logger.info(f"Database: {config.db_path}")
    logger.info(f"Paper traders: {len(traders)}")
    logger.info("-" * 70)
    logger.info("TRADER CONFIGURATIONS:")
    for t in traders[:5]:
        cfg = t.config
        logger.info(f"  {cfg.trader_id}: YES<{cfg.yes_threshold}, NO<{cfg.no_threshold}, Profit>{cfg.profit_threshold:.1%}")
    if len(traders) > 5:
        logger.info(f"  ... and {len(traders) - 5} more")
    logger.info("=" * 70)
    logger.info("Press Ctrl+C to stop\n")

    scan_count = 0
    last_status_time = time.time()
    status_interval = 30  # Print status every 30 seconds

    try:
        while True:
            scan_start = time.time()
            scan_count += 1
            current_ts = int(time.time())

            # Scan markets
            markets = scanner.scan_all_markets()

            # Process each market
            for mkt in markets:
                # Store in Rust collector
                is_opp = collector.process_update(
                    mkt['slug'],
                    mkt['coin'],
                    mkt['yes_token_id'],
                    mkt['no_token_id'],
                    mkt['yes_ask'],
                    mkt['yes_ask_size'],
                    mkt['yes_bid'],
                    mkt['yes_bid_size'],
                    mkt['no_ask'],
                    mkt['no_ask_size'],
                    mkt['no_bid'],
                    mkt['no_bid_size'],
                    mkt['window_end_ts'],
                    mkt['seconds_remaining'],
                    mkt['orderbook_json'],
                )

                # Evaluate with each paper trader
                for trader in traders:
                    trade = trader.evaluate(
                        mkt['slug'],
                        mkt['coin'],
                        mkt['yes_ask'],
                        mkt['no_ask'],
                        mkt['yes_ask_size'],
                        mkt['no_ask_size'],
                        mkt['window_end_ts'],
                        current_ts,
                    )

                    if trade:
                        logger.info(
                            f"TRADE [{trader.trader_id}]: {mkt['coin']} "
                            f"Combined={mkt['yes_ask']+mkt['no_ask']:.4f} "
                            f"Size=${trade['trade_size']:.2f}"
                        )

            # Check for closures across all traders
            for trader in traders:
                closed = trader.check_closures(current_ts)
                for pos in closed:
                    logger.info(
                        f"CLOSED [{trader.trader_id}]: {pos['coin']} "
                        f"Net=${pos['net_pnl']:+.2f}"
                    )

            scan_elapsed = time.time() - scan_start

            # Quick per-scan output
            if markets:
                best = min(markets, key=lambda x: x['yes_ask'] + x['no_ask'])
                combined = best['yes_ask'] + best['no_ask']
                logger.info(
                    f"#{scan_count} Best:{best['coin']}={combined:.3f} "
                    f"Mkts:{len(markets)} Scan:{scan_elapsed*1000:.0f}ms"
                )

            # Periodic detailed status update
            if time.time() - last_status_time > status_interval:
                stats = collector.get_stats()
                db_stats = collector.get_db_stats()

                # Find best and worst traders
                trader_stats = [t.get_stats() for t in traders]
                best = max(trader_stats, key=lambda x: x['total_pnl'])
                worst = min(trader_stats, key=lambda x: x['total_pnl'])
                active = sum(1 for t in trader_stats if t['trades'] > 0)

                logger.info("-" * 70)
                logger.info(
                    f"SCAN #{scan_count} | Updates/sec: {stats['updates_per_second']:.1f} | "
                    f"Stored: {db_stats['snapshot_count']} | Opps: {stats['opportunities_detected']}"
                )
                logger.info(
                    f"TRADERS | Active: {active}/{len(traders)} | "
                    f"Best: {best['trader_id']} ${best['total_pnl']:+.2f} | "
                    f"Worst: {worst['trader_id']} ${worst['total_pnl']:+.2f}"
                )
                logger.info("-" * 70)

                last_status_time = time.time()

            # Sleep to maintain scan rate
            sleep_time = max(0, (config.scan_interval_ms / 1000) - scan_elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("\n" + "=" * 70)
        logger.info("COLLECTOR STOPPED")
        logger.info("=" * 70)

        # Final stats
        stats = collector.get_stats()
        db_stats = collector.get_db_stats()

        logger.info(f"\nCOLLECTION STATS:")
        logger.info(f"  Scans: {scan_count}")
        logger.info(f"  Updates received: {stats['updates_received']}")
        logger.info(f"  Updates stored: {stats['updates_stored']}")
        logger.info(f"  Opportunities: {stats['opportunities_detected']}")
        logger.info(f"  Updates/second: {stats['updates_per_second']:.1f}")
        logger.info(f"  Database size: {db_stats.get('db_size_bytes', 0) / 1024:.1f} KB")

        logger.info(f"\nTRADER PERFORMANCE:")
        trader_stats = sorted(
            [t.get_stats() for t in traders],
            key=lambda x: x['total_pnl'],
            reverse=True
        )

        for i, ts in enumerate(trader_stats[:10]):
            logger.info(
                f"  {i+1}. {ts['trader_id']}: "
                f"YES<{ts['yes_threshold']}, NO<{ts['no_threshold']}, P>{ts['profit_threshold']:.1%} | "
                f"Trades={ts['trades']}, Win={ts['win_rate']:.0f}%, PnL=${ts['total_pnl']:+.2f}"
            )

        logger.info("=" * 70)

    finally:
        collector.stop()
        scanner.shutdown()


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Rust Data Collector - Strategic Research Pipeline")

    parser.add_argument("--interval", type=int, default=100, help="Scan interval in ms (default: 100)")
    parser.add_argument("--db-path", type=str, default="data/gabagool_data.db", help="Database path")
    parser.add_argument("--min-margin", type=float, default=0.005, help="Min margin for opportunity logging")
    parser.add_argument("--store-orderbook", action="store_true", help="Store full orderbook JSON")
    parser.add_argument("--traders", type=int, default=10, help="Number of paper traders (default: 10)")

    args = parser.parse_args()

    config = CollectorConfig(
        scan_interval_ms=args.interval,
        db_path=args.db_path,
        min_margin=args.min_margin,
        store_orderbook=args.store_orderbook,
        num_traders=args.traders,
    )

    run_collector(config)


if __name__ == "__main__":
    main()
