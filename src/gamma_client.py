#!/usr/bin/env python3
"""
Gabagool Bot - Gamma API Client (Market Discovery)

Purpose:
    Client for Polymarket's Gamma API.
    Used for market discovery, metadata, and resolution status.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Based on: samples/discountry-base/src/gamma_client.py
    Key patterns:
        - Slug-based 15-minute market discovery
        - Token ID and price parsing

Dependencies:
    - requests (via ThreadLocalSessionMixin)
    - logging

Usage:
    from src.gamma_client import GammaClient

    client = GammaClient()
    market = client.get_current_15m_market("BTC")
    if market:
        info = client.get_market_info("BTC")
        print(f"Token IDs: {info['token_ids']}")
        print(f"Prices: {info['prices']}")

Notes:
    - Gamma API provides market metadata (not trading)
    - 15-minute markets use slug pattern: {coin}-updown-15m-{timestamp}
    - Synchronous client (use asyncio.to_thread for async contexts)
"""

import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timezone

from .http import ThreadLocalSessionMixin


@dataclass
class Market:
    """
    Represents a Polymarket market.

    Attributes:
        id: Market ID (same as slug for 15-min markets)
        slug: Market slug identifier
        question: Market question/title
        description: Market description
        yes_token_id: YES/Up token ID
        no_token_id: NO/Down token ID
        end_date: When market resolves
        active: Whether market is currently tradeable
        closed: Whether market is closed
        resolved: Whether market has resolved
        volume: Total trading volume
    """
    id: str
    condition_id: str
    slug: str
    question: str
    description: str
    yes_token_id: str
    no_token_id: str
    end_date: Optional[datetime]
    active: bool
    closed: bool
    resolved: bool
    volume: float = 0.0
    category: str = ""


class GammaClient(ThreadLocalSessionMixin):
    """
    Client for Polymarket Gamma API.

    Provides market discovery via slug-based lookups.
    Reference: samples/discountry-base/src/gamma_client.py
    """

    DEFAULT_HOST = "https://gamma-api.polymarket.com"

    # Supported coins/assets and their slug prefixes
    COIN_SLUGS = {
        "BTC": "btc-updown-15m",
        "ETH": "eth-updown-15m",
        "SOL": "sol-updown-15m",
        "XRP": "xrp-updown-15m",
        "MSTR": "mstr-updown-15m",  # MicroStrategy stock (tracks BTC)
    }

    # 5-minute windows (gabagool22 does 76% of volume here)
    COIN_SLUGS_5M = {
        "BTC": "btc-updown-5m",
        "ETH": "eth-updown-5m",
        "SOL": "sol-updown-5m",
        "XRP": "xrp-updown-5m",
    }

    # 1-hour windows (gabagool22 does 24% of volume here)
    COIN_SLUGS_1H = {
        "BTC": "bitcoin-up-or-down",
        "ETH": "ethereum-up-or-down",
    }

    def __init__(self, host: str = None, timeout: int = 10):
        """
        Initialize Gamma API client.

        Args:
            host: API host URL (defaults to production)
            timeout: Request timeout in seconds
        """
        super().__init__()
        self.host = (host or self.DEFAULT_HOST).rstrip("/")
        self.timeout = timeout
        self.logger = logging.getLogger("gamma_client")

    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Get market data by slug.

        Args:
            slug: Market slug (e.g., "btc-updown-15m-1766671200")

        Returns:
            Market data dictionary or None if not found
        """
        url = f"{self.host}/markets/slug/{slug}"

        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                return response.json()
            self.logger.debug("Market %s not found (status %d)", slug, response.status_code)
            return None
        except Exception as e:
            self.logger.error("Error fetching market %s: %s", slug, e)
            return None

    def get_current_15m_market(self, coin: str) -> Optional[Dict[str, Any]]:
        """
        Get the current active 15-minute market for a coin.

        Args:
            coin: Coin symbol (BTC, ETH, SOL, XRP)

        Returns:
            Market data for the current 15-minute window, or None
        """
        coin = coin.upper()
        if coin not in self.COIN_SLUGS:
            self.logger.error("Unsupported coin: %s. Use: %s", coin, list(self.COIN_SLUGS.keys()))
            return None

        prefix = self.COIN_SLUGS[coin]

        # Calculate current 15-minute window timestamp
        now = datetime.now(timezone.utc)
        minute = (now.minute // 15) * 15
        current_window = now.replace(minute=minute, second=0, microsecond=0)
        current_ts = int(current_window.timestamp())

        # Try current window
        slug = f"{prefix}-{current_ts}"
        market = self.get_market_by_slug(slug)
        if market and market.get("acceptingOrders"):
            self.logger.debug("Found active market: %s", slug)
            return market

        # Try next window (in case current just ended)
        next_ts = current_ts + 900  # 15 minutes
        slug = f"{prefix}-{next_ts}"
        market = self.get_market_by_slug(slug)
        if market and market.get("acceptingOrders"):
            self.logger.debug("Found active market (next window): %s", slug)
            return market

        # Try previous window (might still be active)
        prev_ts = current_ts - 900
        slug = f"{prefix}-{prev_ts}"
        market = self.get_market_by_slug(slug)
        if market and market.get("acceptingOrders"):
            self.logger.debug("Found active market (prev window): %s", slug)
            return market

        self.logger.warning("No active 15m market found for %s", coin)
        return None

    def get_current_5m_market(self, coin: str) -> Optional[Dict[str, Any]]:
        """
        Get the current active 5-minute market for a coin.
        Gabagool22 does 76% of volume on 5-minute windows.
        """
        coin = coin.upper()
        if coin not in self.COIN_SLUGS_5M:
            return None

        prefix = self.COIN_SLUGS_5M[coin]
        now = datetime.now(timezone.utc)
        minute = (now.minute // 5) * 5
        current_window = now.replace(minute=minute, second=0, microsecond=0)
        current_ts = int(current_window.timestamp())

        # Try current window
        slug = f"{prefix}-{current_ts}"
        market = self.get_market_by_slug(slug)
        if market and market.get("acceptingOrders"):
            return market

        # Try next window
        next_ts = current_ts + 300  # 5 minutes
        slug = f"{prefix}-{next_ts}"
        market = self.get_market_by_slug(slug)
        if market and market.get("acceptingOrders"):
            return market

        # Try previous window
        prev_ts = current_ts - 300
        slug = f"{prefix}-{prev_ts}"
        market = self.get_market_by_slug(slug)
        if market and market.get("acceptingOrders"):
            return market

        return None

    def get_current_1h_market(self, coin: str) -> Optional[Dict[str, Any]]:
        """
        Get the current active 1-hour market for a coin.
        Uses slug pattern: '{coin}-up-or-down-{month}-{day}-{hour}'
        """
        coin = coin.upper()
        if coin not in self.COIN_SLUGS_1H:
            return None

        now = datetime.now(timezone.utc)
        # 1-hour windows use descriptive slugs not timestamps
        # Pattern from history: "bitcoin-up-or-down-february-20-3am-et"
        # or "ethereum-up-or-down-february-20-4am-et"
        months = ["", "january", "february", "march", "april", "may", "june",
                  "july", "august", "september", "october", "november", "december"]
        month_name = months[now.month]
        day = now.day

        # Convert UTC hour to ET (EST = UTC-5, EDT = UTC-4)
        # Use -4 for EDT (summer), -5 for EST (winter)
        et_hour = (now.hour - 4) % 24  # approximate EDT
        if et_hour == 0:
            hour_str = "12am"
        elif et_hour < 12:
            hour_str = f"{et_hour}am"
        elif et_hour == 12:
            hour_str = "12pm"
        else:
            hour_str = f"{et_hour - 12}pm"

        base = self.COIN_SLUGS_1H[coin]
        slug = f"{base}-{month_name}-{day}-{hour_str}-et"
        market = self.get_market_by_slug(slug)
        if market and market.get("acceptingOrders"):
            return market

        return None

    def parse_token_ids(self, market: Dict[str, Any]) -> Dict[str, str]:
        """
        Parse token IDs from market data.

        Args:
            market: Market data dictionary

        Returns:
            Dictionary with "up"/"down" (or "yes"/"no") token IDs
        """
        clob_token_ids = market.get("clobTokenIds", "[]")
        token_ids = self._parse_json_field(clob_token_ids)

        outcomes = market.get("outcomes", '["Up", "Down"]')
        outcomes = self._parse_json_field(outcomes)

        return self._map_outcomes(outcomes, token_ids)

    def parse_prices(self, market: Dict[str, Any]) -> Dict[str, float]:
        """
        Parse current prices from market data.

        Args:
            market: Market data dictionary

        Returns:
            Dictionary with "up"/"down" (or "yes"/"no") prices
        """
        outcome_prices = market.get("outcomePrices", '["0.5", "0.5"]')
        prices = self._parse_json_field(outcome_prices)

        outcomes = market.get("outcomes", '["Up", "Down"]')
        outcomes = self._parse_json_field(outcomes)

        return self._map_outcomes(outcomes, prices, cast=float)

    @staticmethod
    def _parse_json_field(value: Any) -> List[Any]:
        """Parse a field that may be a JSON string or a list."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return []
        return value if isinstance(value, list) else []

    @staticmethod
    def _map_outcomes(
        outcomes: List[Any],
        values: List[Any],
        cast=lambda v: v
    ) -> Dict[str, Any]:
        """Map outcome labels to values with optional casting."""
        result: Dict[str, Any] = {}
        for i, outcome in enumerate(outcomes):
            if i < len(values):
                try:
                    result[str(outcome).lower()] = cast(values[i])
                except (ValueError, TypeError):
                    pass
        return result

    def get_market_info(self, coin: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive market info for current 15-minute market.

        Args:
            coin: Coin symbol (BTC, ETH, SOL, XRP)

        Returns:
            Dictionary with market info including token IDs and prices
        """
        market = self.get_current_15m_market(coin)
        if not market:
            return None

        token_ids = self.parse_token_ids(market)
        prices = self.parse_prices(market)

        return {
            "slug": market.get("slug"),
            "question": market.get("question"),
            "end_date": market.get("endDate"),
            "token_ids": token_ids,
            "prices": prices,
            "accepting_orders": market.get("acceptingOrders", False),
            "best_bid": market.get("bestBid"),
            "best_ask": market.get("bestAsk"),
            "spread": market.get("spread"),
            "volume": market.get("volume"),
            "raw": market,
        }

    def parse_market(self, data: Dict[str, Any]) -> Market:
        """
        Parse market dict into Market object.

        Args:
            data: Raw market dict from API

        Returns:
            Market object
        """
        # Parse end date if present
        end_date = None
        end_date_str = data.get("endDate") or data.get("end_date_iso")
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(
                    end_date_str.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        # Get token IDs
        token_ids = self.parse_token_ids(data)
        # Map up/down to yes/no for consistency
        yes_token_id = token_ids.get("up") or token_ids.get("yes", "")
        no_token_id = token_ids.get("down") or token_ids.get("no", "")

        return Market(
            id=data.get("id", data.get("slug", "")),
            condition_id=data.get("conditionId", ""),
            slug=data.get("slug", ""),
            question=data.get("question", ""),
            description=data.get("description", ""),
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            end_date=end_date,
            active=data.get("acceptingOrders", False),
            closed=data.get("closed", False),
            resolved=data.get("resolved", False),
            volume=float(data.get("volume", 0) or 0),
            category=data.get("category", "")
        )

    def get_all_active_markets(self, coins: List[str] = None) -> List[Market]:
        """
        Get all active markets across ALL durations (15m, 1h) for specified coins.

        Matches the true Gabagool strategy:
        - 15-minute windows
        - 1-hour windows
        """
        if coins is None:
            coins = list(self.COIN_SLUGS.keys())

        markets = []
        seen_ids = set()

        for coin in coins:

            # 15-minute markets
            market_data = self.get_current_15m_market(coin.upper())
            if market_data:
                parsed = self.parse_market(market_data)
                if parsed.id not in seen_ids:
                    markets.append(parsed)
                    seen_ids.add(parsed.id)

            # 1-hour markets
            market_data = self.get_current_1h_market(coin.upper())
            if market_data:
                parsed = self.parse_market(market_data)
                if parsed.id not in seen_ids:
                    markets.append(parsed)
                    seen_ids.add(parsed.id)

        return markets


def find_15min_markets(coins: List[str] = None) -> List[Market]:
    """
    Find active 15-minute markets for specified coins.

    Convenience function that creates a client and fetches markets.

    Args:
        coins: List of coin symbols (e.g., ['BTC', 'ETH', 'SOL'])

    Returns:
        List of Market objects for 15-minute markets
    """
    client = GammaClient()
    return client.get_all_active_markets(coins)
