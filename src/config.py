#!/usr/bin/env python3
"""
Gabagool Bot - Configuration Module

Purpose:
    Configuration management for the gabagool bot. Loads settings from
    YAML files and environment variables with sensible defaults.

Author: AI-Generated (adapted from discountry/polymarket-trading-bot)
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Based on: samples/discountry-base/src/config.py
    Adapted for gabagool arbitrage strategy

Dependencies:
    - pyyaml
    - python-dotenv

Usage:
    from src.config import Config, GabagoolConfig

    # Load from environment
    config = Config.from_env()

    # Load from YAML with env override
    config = Config.load_with_env("config/default.yaml")

Notes:
    - Environment variables override YAML config
    - All env vars prefixed with POLY_
    - Gabagool-specific settings in GabagoolConfig
"""

import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

try:
    import yaml
except ImportError:
    yaml = None

from dotenv import load_dotenv

# Load .env file if present (check multiple locations)
# Priority: .env in cwd, then config/.env relative to this file
_config_dir = Path(__file__).parent.parent / "config"
load_dotenv(_config_dir / ".env")  # config/.env
load_dotenv()  # .env in cwd (overrides)

# Environment variable prefix
ENV_PREFIX = "POLY_"


def get_env(name: str, default: str = "") -> str:
    """Get environment variable with prefix."""
    return os.environ.get(f"{ENV_PREFIX}{name}", default)


def get_env_bool(name: str, default: bool = False) -> bool:
    """Get boolean environment variable."""
    val = get_env(name, "").lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def get_env_int(name: str, default: int = 0) -> int:
    """Get integer environment variable."""
    val = get_env(name, "")
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return default


def get_env_float(name: str, default: float = 0.0) -> float:
    """Get float environment variable."""
    val = get_env(name, "")
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    return default


class ConfigError(Exception):
    """Base exception for configuration errors."""
    pass


class ConfigNotFoundError(ConfigError):
    """Raised when config file is not found."""
    pass


@dataclass
class ClobConfig:
    """CLOB (Central Limit Order Book) configuration."""
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    # Polymarket signature type for the funder address:
    #   0 = EOA, 1 = POLY_PROXY (most accounts), 2 = POLY_GNOSIS_SAFE.
    # POLY_PROXY (1) covers users whose Polymarket "Safe/Proxy" is an
    # EIP-1167 minimal proxy (the default for non-Safe signups).
    signature_type: int = 3

    def is_valid(self) -> bool:
        """Validate CLOB configuration."""
        return bool(self.host and self.host.startswith("http"))


@dataclass
class SweeperConfig:
    """Automated Profit Sweeper configuration."""
    enabled: bool = False
    target_address: str = ""
    reserve_bankroll: float = 1000.0
    sweep_threshold: float = 5000.0
    do_test_transfer: bool = True

@dataclass
class GabagoolConfig:
    """
    Gabagool arbitrage strategy configuration.

    These are the core parameters for the arbitrage strategy.
    """
    # Entry thresholds
    yes_buy_threshold: float = 0.48
    no_buy_threshold: float = 0.48
    max_combined_cost: float = 0.97
    min_profit_margin: float = 0.02

    # Capital & compounding
    session_capital_usd: float = 100.0
    auto_compound_pct: float = 0.0

    # Position limits
    max_position_per_market: float = 100.0
    max_total_exposure: float = 500.0
    max_concurrent_arbitrages: int = 3
    max_unpaired_exposure: float = 50.0

    # Time limits
    holding_time_limit: int = 1800  # 30 minutes in seconds
    min_time_to_resolution: int = 120  # 2 minutes
    redeem_check_interval: int = 300  # 5 minutes

    # Circuit breakers
    max_consecutive_failures: int = 3
    max_daily_drawdown_pct: float = 0.15
    min_wallet_balance: float = 10.0

    # Target markets
    target_assets: List[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    market_duration_minutes: int = 15


@dataclass
class Config:
    """
    Main configuration class for the gabagool bot.

    Attributes:
        safe_address: The Polymarket Safe/Proxy wallet address
        private_key: Private key for signing (loaded from env, not stored)
        rpc_url: Polygon RPC URL for blockchain calls
        clob: CLOB API configuration
        gabagool: Gabagool strategy configuration
        data_dir: Directory for storing credentials and data
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        dry_run: If True, simulate trades without executing
    """

    # Core settings
    safe_address: str = ""
    rpc_url: str = "https://polygon-rpc.com"

    # API configuration
    clob: ClobConfig = field(default_factory=ClobConfig)

    # Strategy configuration
    gabagool: GabagoolConfig = field(default_factory=GabagoolConfig)

    # Sweeper configuration
    sweeper: SweeperConfig = field(default_factory=SweeperConfig)

    # Paths
    data_dir: str = "data"
    log_dir: str = "logs"
    db_path: str = "data/gabagool.db"

    # Logging
    log_level: str = "INFO"

    # Mode
    dry_run: bool = False

    def __post_init__(self):
        """Validate and normalize configuration."""
        if self.safe_address:
            self.safe_address = self.safe_address.lower()

    @classmethod
    def load(cls, filepath: str = "config/default.yaml") -> "Config":
        """
        Load configuration from YAML file.

        Args:
            filepath: Path to YAML config file

        Returns:
            Config instance
        """
        if yaml is None:
            raise ConfigError("PyYAML not installed. Run: pip install pyyaml")

        path = Path(filepath)

        if not path.exists():
            raise ConfigNotFoundError(f"Config file not found: {filepath}")

        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """Create Config from dictionary."""
        config = cls()

        # Core settings
        if "safe_address" in data:
            config.safe_address = data["safe_address"]
        if "rpc_url" in data:
            config.rpc_url = data["rpc_url"]

        # CLOB config
        if "clob" in data:
            clob_data = data["clob"]
            config.clob = ClobConfig(
                host=clob_data.get("host", config.clob.host),
                chain_id=clob_data.get("chain_id", config.clob.chain_id),
                signature_type=clob_data.get("signature_type", config.clob.signature_type),
            )

        # Gabagool config
        if "gabagool" in data:
            g = data["gabagool"]
            config.gabagool = GabagoolConfig(
                yes_buy_threshold=g.get("yes_buy_threshold", config.gabagool.yes_buy_threshold),
                no_buy_threshold=g.get("no_buy_threshold", config.gabagool.no_buy_threshold),
                max_combined_cost=g.get("max_combined_cost", config.gabagool.max_combined_cost),
                min_profit_margin=g.get("min_profit_margin", config.gabagool.min_profit_margin),
                session_capital_usd=g.get("session_capital_usd", config.gabagool.session_capital_usd),
                auto_compound_pct=g.get("auto_compound_pct", config.gabagool.auto_compound_pct),
                max_position_per_market=g.get("max_position_per_market", config.gabagool.max_position_per_market),
                max_total_exposure=g.get("max_total_exposure", config.gabagool.max_total_exposure),
                max_concurrent_arbitrages=g.get("max_concurrent_arbitrages", config.gabagool.max_concurrent_arbitrages),
                max_unpaired_exposure=g.get("max_unpaired_exposure", config.gabagool.max_unpaired_exposure),
                holding_time_limit=g.get("holding_time_limit", config.gabagool.holding_time_limit),
                min_time_to_resolution=g.get("min_time_to_resolution", config.gabagool.min_time_to_resolution),
                redeem_check_interval=g.get("redeem_check_interval", config.gabagool.redeem_check_interval),
                max_consecutive_failures=g.get("max_consecutive_failures", config.gabagool.max_consecutive_failures),
                max_daily_drawdown_pct=g.get("max_daily_drawdown_pct", config.gabagool.max_daily_drawdown_pct),
                min_wallet_balance=g.get("min_wallet_balance", config.gabagool.min_wallet_balance),
                target_assets=g.get("target_assets", config.gabagool.target_assets),
                market_duration_minutes=g.get("market_duration_minutes", config.gabagool.market_duration_minutes),
            )

            # Sweeper can be nested under gabagool: in production.yaml
            if "sweeper" in g:
                s = g["sweeper"]
                config.sweeper = SweeperConfig(
                    enabled=s.get("enabled", config.sweeper.enabled),
                    target_address=s.get("target_address", config.sweeper.target_address),
                    reserve_bankroll=s.get("reserve_bankroll", config.sweeper.reserve_bankroll),
                    sweep_threshold=s.get("sweep_threshold", config.sweeper.sweep_threshold),
                    do_test_transfer=s.get("do_test_transfer", config.sweeper.do_test_transfer),
                )

        # Sweeper config
        if "sweeper" in data:
            s = data["sweeper"]
            config.sweeper = SweeperConfig(
                enabled=s.get("enabled", config.sweeper.enabled),
                target_address=s.get("target_address", config.sweeper.target_address),
                reserve_bankroll=s.get("reserve_bankroll", config.sweeper.reserve_bankroll),
                sweep_threshold=s.get("sweep_threshold", config.sweeper.sweep_threshold),
                do_test_transfer=s.get("do_test_transfer", config.sweeper.do_test_transfer),
            )

        # Paths
        if "data_dir" in data:
            config.data_dir = data["data_dir"]
        if "log_dir" in data:
            config.log_dir = data["log_dir"]
        if "db_path" in data:
            config.db_path = data["db_path"]

        # Logging
        if "log_level" in data:
            config.log_level = data["log_level"]

        # Mode
        if "dry_run" in data:
            config.dry_run = data["dry_run"]

        return config

    @classmethod
    def from_env(cls) -> "Config":
        """
        Load configuration from environment variables.

        Environment variables (all prefixed with POLY_):
            SAFE_ADDRESS: Polymarket Safe/Proxy wallet address
            PRIVATE_KEY: Private key (not stored in config)
            RPC_URL: Polygon RPC URL
            CLOB_HOST: CLOB API host
            CHAIN_ID: Chain ID (default: 137)
            DATA_DIR: Data directory
            LOG_LEVEL: Logging level
            DRY_RUN: Enable dry run mode

        Returns:
            Config instance
        """
        config = cls()

        # Core settings
        safe_address = get_env("SAFE_ADDRESS")
        if safe_address:
            config.safe_address = safe_address

        rpc_url = get_env("RPC_URL")
        if rpc_url:
            config.rpc_url = rpc_url

        # CLOB config
        clob_host = get_env("CLOB_HOST")
        chain_id = get_env_int("CHAIN_ID", 137)
        if clob_host:
            config.clob = ClobConfig(
                host=clob_host,
                chain_id=chain_id,
            )
        elif chain_id != 137:
            config.clob.chain_id = chain_id

        # Gabagool settings from env
        max_exposure = get_env_float("MAX_EXPOSURE")
        if max_exposure:
            config.gabagool.max_total_exposure = max_exposure

        max_position = get_env_float("MAX_POSITION")
        if max_position:
            config.gabagool.max_position_per_market = max_position

        # Other settings
        data_dir = get_env("DATA_DIR")
        if data_dir:
            config.data_dir = data_dir

        log_level = get_env("LOG_LEVEL")
        if log_level:
            config.log_level = log_level.upper()

        config.dry_run = get_env_bool("DRY_RUN", False)

        return config

    @classmethod
    def load_with_env(cls, filepath: str = "config/default.yaml") -> "Config":
        """
        Load configuration from YAML file with environment variable overrides.

        Args:
            filepath: Path to YAML config file

        Returns:
            Config instance with env vars taking precedence
        """
        # Start with YAML config if it exists
        path = Path(filepath)
        if path.exists():
            config = cls.load(filepath)
        else:
            config = cls()

        # Override with environment variables
        safe_address = get_env("SAFE_ADDRESS")
        if safe_address:
            config.safe_address = safe_address.lower()

        rpc_url = get_env("RPC_URL")
        if rpc_url:
            config.rpc_url = rpc_url

        # Other settings
        data_dir = get_env("DATA_DIR")
        if data_dir:
            config.data_dir = data_dir

        log_level = get_env("LOG_LEVEL")
        if log_level:
            config.log_level = log_level.upper()

        if get_env("DRY_RUN"):
            config.dry_run = get_env_bool("DRY_RUN")

        return config

    def get_private_key(self) -> Optional[str]:
        """
        Get private key from environment.

        Private key is never stored in config, always read from env.

        Returns:
            Private key string or None if not set
        """
        return get_env("PRIVATE_KEY") or None

    def validate(self) -> List[str]:
        """
        Validate configuration.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        if not self.safe_address:
            errors.append("safe_address is required")

        if not self.get_private_key():
            errors.append("POLY_PRIVATE_KEY environment variable is required")

        if not self.clob.is_valid():
            errors.append("clob configuration is invalid")

        if self.gabagool.max_combined_cost >= 1.0:
            errors.append("max_combined_cost must be less than 1.0")

        if self.gabagool.min_profit_margin <= 0:
            errors.append("min_profit_margin must be positive")

        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary (excludes private key)."""
        return {
            "safe_address": self.safe_address,
            "rpc_url": self.rpc_url,
            "clob": asdict(self.clob),
            "gabagool": asdict(self.gabagool),
            "data_dir": self.data_dir,
            "log_dir": self.log_dir,
            "db_path": self.db_path,
            "log_level": self.log_level,
            "dry_run": self.dry_run,
        }

    def __repr__(self) -> str:
        """String representation (no sensitive data)."""
        addr = self.safe_address[:10] + "..." if self.safe_address else "not set"
        return (
            f"Config(safe_address={addr}, "
            f"dry_run={self.dry_run}, "
            f"exposure_limit=${self.gabagool.max_total_exposure})"
        )
