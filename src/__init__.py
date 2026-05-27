#!/usr/bin/env python3
"""
Gabagool Bot - Source Package

Purpose:
    Main package for the Gabagool Polymarket arbitrage bot.
    Exports core components for use by strategies and scripts.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Components:
    Core Infrastructure (from discountry):
    - Config: Configuration management
    - OrderSigner: EIP-712 order signing
    - ClobClient: CLOB API client
    - GammaClient: Market discovery API
    - TradingBot: High-level trading interface

    Strategy Components:
    - PositionTracker: Thread-safe position tracking (from Trust412)
    - RiskManager: Pre-trade validation (from lorine93s)
    - StatsTracker: Performance metrics (from warproxxx)
    - TradingDatabase: SQLite persistence

    Advanced Features:
    - AutoRedeemer: Settlement automation (from lorine93s)
    - PositionMerger: Gas optimization (from warproxxx)

Usage:
    from src import TradingBot, Config
    from src import PositionTracker, RiskManager
"""

__version__ = "1.0.0"
__author__ = "Gabagool Bot Team"

# =============================================================================
# Core Infrastructure (Phase 1 - Implemented)
# =============================================================================

# Configuration
from .config import Config, ClobConfig, GabagoolConfig, ConfigError

# Cryptography
from .crypto import KeyManager, InvalidPasswordError, verify_private_key

# Order signing
from .signer import OrderSigner, Order, SignerError

# API clients
from .client import (
    ClobClient,
    GammaClient,
    ApiClient,
    ApiCredentials,
    ApiError,
    AuthenticationError,
    OrderError,
    RateLimitError,
)

# Trading bot
from .bot import TradingBot, BotConfig, create_bot_from_config

# =============================================================================
# Strategy Components (Already Implemented)
# =============================================================================

# Position tracking
from .position_tracker import PositionTracker, ArbitragePosition

# Risk management
from .risk_manager import RiskManager, RiskConfig

# Statistics tracking
from .stats_tracker import StatsTracker

# Database
from .db import TradingDatabase

# =============================================================================
# Advanced Features (Scaffolds - TODO)
# =============================================================================

# Uncomment as implemented:
# from .auto_redeem import AutoRedeemer
# from .poly_merger import PositionMerger
# from .gamma_client import GammaClient as GammaMarketClient
# from .websocket_client import WebSocketClient

# =============================================================================
# Public API
# =============================================================================

__all__ = [
    # Version
    "__version__",

    # Configuration
    "Config",
    "ClobConfig",
    "GabagoolConfig",
    "ConfigError",

    # Cryptography
    "KeyManager",
    "InvalidPasswordError",
    "verify_private_key",

    # Signing
    "OrderSigner",
    "Order",
    "SignerError",

    # API Clients
    "ClobClient",
    "GammaClient",
    "ApiClient",
    "ApiCredentials",
    "ApiError",
    "AuthenticationError",
    "OrderError",
    "RateLimitError",
    "create_authenticated_client",

    # Trading Bot
    "TradingBot",
    "BotConfig",
    "create_bot_from_config",

    # Position Tracking
    "PositionTracker",
    "ArbitragePosition",

    # Risk Management
    "RiskManager",
    "RiskConfig",

    # Statistics
    "StatsTracker",

    # Database
    "TradingDatabase",
]
