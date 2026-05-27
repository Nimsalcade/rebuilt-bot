import uvloop
import asyncio
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
#!/usr/bin/env python3
"""
Gabagool Bot - Main Entry Point

Purpose:
    Main entry point and orchestrator for the Gabagool arbitrage bot.
    Initializes all components and runs the main trading loop.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Dependencies:
    - asyncio
    - logging
    - All src modules

Usage:
    # Dry run mode
    python -m src.main --dry-run

    # Production mode
    python -m src.main

    # With custom config
    python -m src.main --config config/production.yaml

Notes:
    - Components are initialized from multiple source repositories
    - See README.md for architecture overview
    - Run in tmux/screen for production

Data Sources:
    - Config: config/default.yaml, config/production.yaml
    - Environment: config/.env
    - Database: gabagool.db
"""

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

# Use uvloop on Linux for ~2-4x faster event loop (HFT-critical)
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass  # macOS / no uvloop — falls back to default

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Core infrastructure
from src.config import Config
from src.bot import TradingBot, BotConfig, create_bot_from_config

# Support components
from src.risk_manager import RiskManager, RiskConfig
from src.stats_tracker import StatsTracker
from src.db import TradingDatabase

# Precision snipe-maker strategy (replaces momentum_maker)
from strategies.snipe_maker import SnipeMakerStrategy


# Constants
VERSION = "1.0.0"
DEFAULT_CONFIG = "config/default.yaml"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Global flag for shutdown
shutdown_event = asyncio.Event()


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> None:
    """
    Configure logging for the bot.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directory for log files
    """
    # Create handlers
    handlers = [logging.StreamHandler()]

    # File handler
    log_path = PROJECT_ROOT / log_dir / "gabagool.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(log_path))

    # Configure
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=LOG_FORMAT,
        handlers=handlers
    )

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("py_clob_client_v2").setLevel(logging.CRITICAL)
    logging.getLogger("py_clob_client_v2.http_helpers.helpers").setLevel(logging.CRITICAL)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Gabagool - Polymarket Arbitrage Bot"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_CONFIG,
        help="Path to configuration file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in simulation mode without executing trades"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=5,
        help="Seconds between opportunity scans (default: 5)"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Gabagool Bot v{VERSION}"
    )
    return parser.parse_args()


def setup_signal_handlers() -> None:
    """Setup graceful shutdown handlers for SIGINT and SIGTERM."""
    def handler(sig, frame):
        logging.getLogger("main").info("Shutdown signal received (%s)", sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


class GabagoolBot:
    """
    Main orchestrator for the Gabagool precision snipe-maker bot.

    Wires together all components:
    - TradingBot: Order execution via Polymarket CLOB
    - RiskManager: Pre-trade validation
    - StatsTracker: Performance metrics
    - TradingDatabase: SQLite persistence
    - SnipeMakerStrategy: Latency arb (farming + snipe state machine)
    """

    def __init__(self, config: Config, dry_run: bool = False):
        """
        Initialize all components.

        Args:
            config: Config object with all settings
            dry_run: If True, simulate trades without executing
        """
        self.config = config
        self.dry_run = dry_run or config.dry_run
        self.logger = logging.getLogger("GabagoolBot")

        # Override dry_run in config if CLI flag set
        if dry_run:
            self.config.dry_run = True

        # Components (initialized in initialize_components())
        self.bot: Optional[TradingBot] = None
        self.risk_manager: Optional[RiskManager] = None
        self.stats_tracker: Optional[StatsTracker] = None
        self.db: Optional[TradingDatabase] = None
        self.strategy: Optional[SnipeMakerStrategy] = None

    def initialize_components(self) -> bool:
        """
        Initialize all components.

        Returns:
            True if all components initialized successfully
        """
        self.logger.info("Initializing components...")

        try:
            # 1. Database
            db_path = PROJECT_ROOT / self.config.db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db = TradingDatabase(str(db_path))
            self.logger.info("  [OK] Database: %s", db_path)

            # 2. Trading Bot
            self.bot = create_bot_from_config(self.config)
            self.logger.info("  [OK] TradingBot")

            # 3. Risk Manager
            risk_config = RiskConfig(
                max_position_per_market=self.config.gabagool.max_position_per_market,
                max_total_exposure=self.config.gabagool.max_total_exposure,
                max_concurrent_arbitrages=self.config.gabagool.max_concurrent_arbitrages,
                min_profit_margin=self.config.gabagool.min_profit_margin,
                max_combined_cost=self.config.gabagool.max_combined_cost,
            )
            self.risk_manager = RiskManager(risk_config)
            self.logger.info("  [OK] RiskManager")

            # 4. Stats Tracker
            stats_path = PROJECT_ROOT / self.config.data_dir / "performance_stats.json"
            stats_path.parent.mkdir(parents=True, exist_ok=True)
            self.stats_tracker = StatsTracker(str(stats_path))
            self.logger.info("  [OK] StatsTracker")

            # 5. Snipe Maker Strategy
            gabagool_cfg = self.config.gabagool
            self.strategy = SnipeMakerStrategy(
                bot=self.bot,
                config=self.config.to_dict() if hasattr(self.config, 'to_dict') else vars(self.config),
                dry_run=self.dry_run,
                assets=getattr(gabagool_cfg, 'target_assets', ['BTC', 'ETH', 'SOL']),
                spike_threshold_pct=getattr(gabagool_cfg, 'spike_threshold_pct', 0.02),
            )
            self.logger.info("  [OK] SnipeMakerStrategy")

            self.logger.info("All components initialized successfully")
            return True

        except Exception as e:
            self.logger.error("Component initialization failed: %s", e)
            return False

    def connect(self) -> bool:
        """
        Connect to Polymarket API.

        Returns:
            True if connected successfully
        """
        self.logger.info("Connecting to Polymarket...")

        if not self.bot:
            self.logger.error("Bot not initialized")
            return False

        if not self.bot.connect():
            self.logger.error("Failed to connect to Polymarket API")
            return False

        self.logger.info("Connected to Polymarket API")
        return True

    async def run_loop(self, scan_interval: int = 5) -> None:
        """
        Run the snipe-maker strategy.

        Delegates entirely to SnipeMakerStrategy which manages
        its own window discovery, spike detection, and snipe execution.
        """
        self.logger.info("-" * 60)
        self.logger.info("Starting Precision Snipe-Maker (Latency Arbitrage Mode)")
        self.logger.info("-" * 60)

        await self.strategy.run()

        self.logger.info("Strategy run complete")

    def shutdown(self) -> None:
        """Graceful shutdown."""
        self.logger.info("Shutting down...")

        # Print final stats
        if self.stats_tracker:
            self.stats_tracker.print_summary()

        # Close database
        if self.db:
            self.db.close()

        self.logger.info("Shutdown complete")


async def main():
    """Main entry point for the Gabagool bot."""
    args = parse_args()

    # Setup
    setup_logging(level=args.log_level)
    setup_signal_handlers()
    logger = logging.getLogger("main")

    # Banner
    logger.info("=" * 60)
    logger.info("GABAGOOL ARBITRAGE BOT - v%s", VERSION)
    logger.info("Hybrid approach with best-in-class components")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info(">>> DRY RUN MODE - No trades will be executed <<<")

    # Load configuration
    try:
        config = Config.load_with_env(args.config)
        logger.info("Configuration loaded: %s", config)
    except Exception as e:
        logger.error("Failed to load configuration: %s", e)
        sys.exit(1)

    # Validate configuration
    errors = config.validate()
    if errors:
        logger.error("Configuration validation failed:")
        for error in errors:
            logger.error("  - %s", error)
        sys.exit(1)

    # Create and initialize bot
    gabagool = GabagoolBot(config, dry_run=args.dry_run)

    if not gabagool.initialize_components():
        logger.error("Failed to initialize components")
        sys.exit(1)

    # Connect to API (skip in dry_run for testing)
    if not args.dry_run:
        if not gabagool.connect():
            logger.error("Failed to connect to Polymarket")
            sys.exit(1)
    else:
        logger.info("Skipping API connection in dry-run mode")

    # Run momentum maker strategy
    try:
        await gabagool.run_loop()
    finally:
        gabagool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
