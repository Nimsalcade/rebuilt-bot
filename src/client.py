#!/usr/bin/env python3
"""
Gabagool Bot - Polymarket API Client

Purpose:
    Low-level API clients for Polymarket CLOB and Gamma APIs.
    Handles authentication, request signing, retries, and rate limiting.

Author: AI-Generated (adapted from discountry/polymarket-trading-bot)
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Adapted from: samples/discountry-base/src/client.py

Dependencies:
    - requests
    - aiohttp (for async operations)

Usage:
    from src.client import ClobClient, ApiCredentials
    from src.signer import OrderSigner

    # Initialize
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        funder="0x..."
    )

    # Authenticate
    signer = OrderSigner(private_key)
    creds = client.create_or_derive_api_key(signer)
    client.set_api_creds(creds)

    # Use API
    orderbook = client.get_order_book(token_id)

Notes:
    - Uses HMAC authentication for authenticated endpoints
    - Thread-safe session management via ThreadLocalSessionMixin
    - Automatic retry with exponential backoff
"""

import time
import hmac
import hashlib
import base64
import json
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import requests

from .http import ThreadLocalSessionMixin


# Module logger
logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Base exception for API errors."""
    pass


class AuthenticationError(ApiError):
    """Raised when authentication fails."""
    pass


class OrderError(ApiError):
    """Raised when order operations fail."""
    pass


class RateLimitError(ApiError):
    """Raised when rate limit is exceeded."""
    pass


@dataclass
class ApiCredentials:
    """
    User-level API credentials for CLOB L2 authentication.

    These credentials are derived from an L1 EIP-712 signature
    and used for authenticated endpoints (orders, trades, etc.).
    """
    api_key: str
    secret: str
    passphrase: str

    @classmethod
    def load(cls, filepath: str) -> "ApiCredentials":
        """Load credentials from JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls(
            api_key=data.get("apiKey", ""),
            secret=data.get("secret", ""),
            passphrase=data.get("passphrase", ""),
        )

    def save(self, filepath: str) -> None:
        """Save credentials to JSON file."""
        import os
        from pathlib import Path

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w') as f:
            json.dump({
                "apiKey": self.api_key,
                "secret": self.secret,
                "passphrase": self.passphrase,
            }, f, indent=2)

        os.chmod(path, 0o600)

    def is_valid(self) -> bool:
        """Check if credentials are valid."""
        return bool(self.api_key and self.secret and self.passphrase)


class ApiClient(ThreadLocalSessionMixin):
    """
    Base HTTP client with common functionality.

    Provides:
    - Automatic JSON handling
    - Request/response logging
    - Retry with exponential backoff
    - Thread-safe sessions
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        retry_count: int = 3
    ):
        """
        Initialize API client.

        Args:
            base_url: Base URL for all requests
            timeout: Request timeout in seconds
            retry_count: Number of retries on failure
        """
        super().__init__()
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.retry_count = retry_count
        self.logger = logging.getLogger(self.__class__.__name__)

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Any] = None,
        headers: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Make HTTP request with error handling and retries.

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint
            data: Request body data
            headers: Additional headers
            params: Query parameters

        Returns:
            Response JSON data

        Raises:
            ApiError: On request failure
            RateLimitError: On rate limit (429)
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        request_headers = {"Content-Type": "application/json"}

        if headers:
            request_headers.update(headers)

        last_error = None
        for attempt in range(self.retry_count):
            try:
                session = self.session

                if method.upper() == "GET":
                    response = session.get(
                        url, headers=request_headers,
                        params=params, timeout=self.timeout
                    )
                elif method.upper() == "POST":
                    response = session.post(
                        url, headers=request_headers,
                        json=data, params=params, timeout=self.timeout
                    )
                elif method.upper() == "DELETE":
                    response = session.delete(
                        url, headers=request_headers,
                        json=data, params=params, timeout=self.timeout
                    )
                else:
                    raise ApiError(f"Unsupported method: {method}")

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    self.logger.warning(f"Rate limited, waiting {retry_after}s")
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json() if response.text else {}

            except requests.exceptions.HTTPError as e:
                if e.response is not None:
                    if e.response.status_code == 429:
                        raise RateLimitError("Rate limit exceeded")
                    if e.response.status_code == 404:
                        self.logger.debug(f"404 Not Found for {url} - bypassing retries")
                        raise ApiError(f"404 Not Found: {e}")
                        
                last_error = e
                self.logger.warning(f"HTTP error (attempt {attempt + 1}): {e}")

            except requests.exceptions.RequestException as e:
                last_error = e
                self.logger.warning(f"Request error (attempt {attempt + 1}): {e}")

            if attempt < self.retry_count - 1:
                sleep_time = 2 ** attempt
                self.logger.debug(f"Retrying in {sleep_time}s...")
                time.sleep(sleep_time)

        raise ApiError(f"Request failed after {self.retry_count} attempts: {last_error}")


class ClobClient(ApiClient):
    """
    Client for Polymarket CLOB (Central Limit Order Book) API.

    Features:
    - Order placement and cancellation
    - Order book queries
    - Trade history
    - L2 API key derivation

    Example:
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            funder="0x..."
        )

        # Authenticate
        signer = OrderSigner(private_key)
        creds = client.create_or_derive_api_key(signer)
        client.set_api_creds(creds)

        # Place order
        signed = signer.sign_order_dict(...)
        client.post_order(signed)
    """

    def __init__(
        self,
        host: str = "https://clob.polymarket.com",
        chain_id: int = 137,
        signature_type: int = 2,
        funder: str = "",
        api_creds: Optional[ApiCredentials] = None,
        timeout: int = 30
    ):
        """
        Initialize CLOB client.

        Args:
            host: CLOB API host
            chain_id: Chain ID (137 for Polygon mainnet)
            signature_type: Signature type (2 = Gnosis Safe)
            funder: Funder/Safe address
            api_creds: User API credentials (optional)
            timeout: Request timeout
        """
        super().__init__(base_url=host, timeout=timeout)
        self.host = host
        self.chain_id = chain_id
        self.signature_type = signature_type
        self.funder = funder
        self.api_creds = api_creds

    def _build_headers(
        self,
        method: str,
        path: str,
        body: str = ""
    ) -> Dict[str, str]:
        """
        Build L2 authentication headers.

        Uses HMAC-SHA256 with the API secret.

        Args:
            method: HTTP method
            path: Request path
            body: Request body

        Returns:
            Dictionary of authentication headers
        """
        headers = {}

        if self.api_creds and self.api_creds.is_valid():
            timestamp = str(int(time.time()))

            # Build message: timestamp + method + path + body
            message = f"{timestamp}{method}{path}"
            if body:
                message += body

            # Decode base64 secret and create HMAC signature
            try:
                base64_secret = base64.urlsafe_b64decode(self.api_creds.secret)
                h = hmac.new(base64_secret, message.encode("utf-8"), hashlib.sha256)
                signature = base64.urlsafe_b64encode(h.digest()).decode("utf-8")
            except Exception:
                # Fallback: use secret directly if not base64 encoded
                signature = hmac.new(
                    self.api_creds.secret.encode(),
                    message.encode(),
                    hashlib.sha256
                ).hexdigest()

            headers.update({
                "POLY_ADDRESS": self.funder,
                "POLY_API_KEY": self.api_creds.api_key,
                "POLY_TIMESTAMP": timestamp,
                "POLY_PASSPHRASE": self.api_creds.passphrase,
                "POLY_SIGNATURE": signature,
            })

        return headers

    def derive_api_key(self, signer: "OrderSigner", nonce: int = 0) -> ApiCredentials:
        """
        Derive L2 API credentials using L1 EIP-712 authentication.

        This is required to access authenticated endpoints like
        /orders and /trades.

        Args:
            signer: OrderSigner instance with private key
            nonce: Nonce for the auth message (default 0)

        Returns:
            ApiCredentials with api_key, secret, and passphrase
        """
        timestamp = str(int(time.time()))

        # Sign the auth message using EIP-712
        auth_signature = signer.sign_auth_message(timestamp=timestamp, nonce=nonce)

        # L1 headers
        headers = {
            "POLY_ADDRESS": signer.address,
            "POLY_SIGNATURE": auth_signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_NONCE": str(nonce),
        }

        response = self._request("GET", "/auth/derive-api-key", headers=headers)

        return ApiCredentials(
            api_key=response.get("apiKey", ""),
            secret=response.get("secret", ""),
            passphrase=response.get("passphrase", ""),
        )

    def create_api_key(self, signer: "OrderSigner", nonce: int = 0) -> ApiCredentials:
        """
        Create new L2 API credentials using L1 EIP-712 authentication.

        Use this if derive_api_key fails (first time setup).

        Args:
            signer: OrderSigner instance with private key
            nonce: Nonce for the auth message (default 0)

        Returns:
            ApiCredentials with api_key, secret, and passphrase
        """
        timestamp = str(int(time.time()))

        # Sign the auth message using EIP-712
        auth_signature = signer.sign_auth_message(timestamp=timestamp, nonce=nonce)

        # L1 headers
        headers = {
            "POLY_ADDRESS": signer.address,
            "POLY_SIGNATURE": auth_signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_NONCE": str(nonce),
        }

        response = self._request("POST", "/auth/api-key", headers=headers)

        return ApiCredentials(
            api_key=response.get("apiKey", ""),
            secret=response.get("secret", ""),
            passphrase=response.get("passphrase", ""),
        )

    def create_or_derive_api_key(self, signer: "OrderSigner", nonce: int = 0) -> ApiCredentials:
        """
        Create API credentials if not exists, otherwise derive them.

        Args:
            signer: OrderSigner instance with private key
            nonce: Nonce for the auth message (default 0)

        Returns:
            ApiCredentials with api_key, secret, and passphrase
        """
        try:
            return self.create_api_key(signer, nonce)
        except Exception:
            return self.derive_api_key(signer, nonce)

    def set_api_creds(self, creds: ApiCredentials) -> None:
        """Set API credentials for authenticated requests."""
        self.api_creds = creds
        self.logger.info("API credentials set")

    # =========================================================================
    # Public Endpoints (No Authentication)
    # =========================================================================

    def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """
        Get order book for a token.

        Args:
            token_id: Market token ID

        Returns:
            Order book with bids and asks
        """
        return self._request(
            "GET",
            "/book",
            params={"token_id": token_id}
        )

    def get_price(self, token_id: str, side: str = "BUY") -> float:
        """
        Get current price for a token.

        Args:
            token_id: Market token ID
            side: 'BUY' or 'SELL'

        Returns:
            Current price (0-1)
        """
        data = self._request(
            "GET",
            "/price",
            params={"token_id": token_id, "side": side}
        )
        return float(data.get("price", 0))

    def get_midpoint(self, token_id: str) -> float:
        """
        Get midpoint price for a token.

        Args:
            token_id: Market token ID

        Returns:
            Midpoint price (0-1)
        """
        data = self._request(
            "GET",
            "/midpoint",
            params={"token_id": token_id}
        )
        return float(data.get("mid", 0))

    def get_spread(self, token_id: str) -> Dict[str, float]:
        """
        Get bid-ask spread for a token.

        Args:
            token_id: Market token ID

        Returns:
            Dict with bid, ask, spread
        """
        try:
            book = self.get_order_book(token_id)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 1.0
            
            return {
                "bid": best_bid,
                "ask": best_ask,
                "spread": best_ask - best_bid,
            }
        except Exception as e:
            if "404" in str(e):
                raise ValueError(f"Market Not Found: {e}")
            self.logger.debug("Failed to calculate spread for %s: %s", token_id, e)
            return {"bid": 0.0, "ask": 1.0, "spread": 1.0}

    # =========================================================================
    # Authenticated Endpoints
    # =========================================================================

    def get_open_orders(self, market: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open orders for the funder.

        Args:
            market: Filter by market/condition ID (optional)

        Returns:
            List of open orders
        """
        endpoint = "/data/orders"
        headers = self._build_headers("GET", endpoint)
        params = {}
        if market:
            params["market"] = market

        result = self._request(
            "GET",
            endpoint,
            headers=headers,
            params=params if params else None
        )

        # Handle paginated response
        if isinstance(result, dict) and "data" in result:
            return result.get("data", [])
        return result if isinstance(result, list) else []

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """
        Get order by ID.

        Args:
            order_id: Order ID

        Returns:
            Order details
        """
        endpoint = f"/data/order/{order_id}"
        headers = self._build_headers("GET", endpoint)
        return self._request("GET", endpoint, headers=headers)

    def get_trades(
        self,
        token_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get trade history.

        Args:
            token_id: Filter by token (optional)
            limit: Maximum number of trades

        Returns:
            List of trades
        """
        endpoint = "/data/trades"
        headers = self._build_headers("GET", endpoint)
        params: Dict[str, Any] = {"limit": limit}
        if token_id:
            params["token_id"] = token_id

        result = self._request(
            "GET",
            endpoint,
            headers=headers,
            params=params
        )

        # Handle paginated response
        if isinstance(result, dict) and "data" in result:
            return result.get("data", [])
        return result if isinstance(result, list) else []

    def post_order(
        self,
        signed_order: Dict[str, Any],
        order_type: str = "GTC"
    ) -> Dict[str, Any]:
        """
        Submit a signed order.

        Args:
            signed_order: Order with signature from OrderSigner
            order_type: Order type (GTC, GTD, FOK)
                - GTC: Good Till Cancelled
                - GTD: Good Till Date
                - FOK: Fill Or Kill

        Returns:
            Response with order ID and status
        """
        endpoint = "/order"

        # Build request body
        body = {
            "order": signed_order.get("order", signed_order),
            "owner": self.funder,
            "orderType": order_type,
        }

        # Add signature
        if "signature" in signed_order:
            body["signature"] = signed_order["signature"]

        body_json = json.dumps(body, separators=(',', ':'))
        headers = self._build_headers("POST", endpoint, body_json)

        return self._request(
            "POST",
            endpoint,
            data=body,
            headers=headers
        )

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            Cancellation response
        """
        endpoint = "/order"
        body = {"orderID": order_id}
        body_json = json.dumps(body, separators=(',', ':'))
        headers = self._build_headers("DELETE", endpoint, body_json)

        return self._request(
            "DELETE",
            endpoint,
            data=body,
            headers=headers
        )

    def cancel_orders(self, order_ids: List[str]) -> Dict[str, Any]:
        """
        Cancel multiple orders by their IDs.

        Args:
            order_ids: List of order IDs to cancel

        Returns:
            Cancellation response with canceled and not_canceled lists
        """
        endpoint = "/orders"
        body_json = json.dumps(order_ids, separators=(',', ':'))
        headers = self._build_headers("DELETE", endpoint, body_json)

        return self._request(
            "DELETE",
            endpoint,
            data=order_ids,
            headers=headers
        )

    def cancel_all_orders(self) -> Dict[str, Any]:
        """
        Cancel all open orders.

        Returns:
            Cancellation response with canceled and not_canceled lists
        """
        endpoint = "/cancel-all"
        headers = self._build_headers("DELETE", endpoint)

        return self._request(
            "DELETE",
            endpoint,
            headers=headers
        )

    def cancel_market_orders(
        self,
        market: Optional[str] = None,
        asset_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancel orders for a specific market.

        Args:
            market: Condition ID of the market (optional)
            asset_id: Token/asset ID (optional)

        Returns:
            Cancellation response with canceled and not_canceled lists
        """
        endpoint = "/cancel-market-orders"
        body = {}

        if market:
            body["market"] = market
        if asset_id:
            body["asset_id"] = asset_id

        body_json = json.dumps(body, separators=(',', ':')) if body else ""
        headers = self._build_headers("DELETE", endpoint, body_json)

        return self._request(
            "DELETE",
            endpoint,
            data=body if body else None,
            headers=headers
        )


class GammaClient(ApiClient):
    """
    Client for Polymarket Gamma API (market discovery).

    Gamma API provides:
    - Market listings and metadata
    - Market resolution status
    - Volume and liquidity data

    Example:
        client = GammaClient()
        markets = client.get_markets(active=True)
    """

    def __init__(
        self,
        host: str = "https://gamma-api.polymarket.com",
        timeout: int = 30
    ):
        """
        Initialize Gamma client.

        Args:
            host: Gamma API host
            timeout: Request timeout
        """
        super().__init__(base_url=host, timeout=timeout)

    def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get list of markets.

        Args:
            active: Include active markets
            closed: Include closed markets
            limit: Maximum number of markets
            offset: Pagination offset

        Returns:
            List of market dicts
        """
        params = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
        }

        return self._request("GET", "/markets", params=params)

    def get_market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """
        Get market by condition ID.

        Args:
            condition_id: Market condition ID

        Returns:
            Market details or None
        """
        try:
            return self._request("GET", f"/markets/{condition_id}")
        except ApiError:
            return None

    def search_markets(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search markets by text query.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching markets
        """
        params = {"q": query, "limit": limit}
        return self._request("GET", "/markets/search", params=params)

    def get_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get list of events (market groups).

        Args:
            limit: Maximum number of events

        Returns:
            List of event dicts
        """
        return self._request("GET", "/events", params={"limit": limit})


# Convenience function for creating authenticated client
def create_authenticated_client(
    private_key: str,
    safe_address: str,
    host: str = "https://clob.polymarket.com",
    chain_id: int = 137
) -> ClobClient:
    """
    Create and authenticate a CLOB client.

    Args:
        private_key: EOA private key
        safe_address: Polymarket Safe/Proxy address
        host: CLOB API host
        chain_id: Chain ID

    Returns:
        Authenticated ClobClient
    """
    from .signer import OrderSigner

    client = ClobClient(
        host=host,
        chain_id=chain_id,
        funder=safe_address,
    )

    signer = OrderSigner(private_key)
    creds = client.create_or_derive_api_key(signer)
    client.set_api_creds(creds)

    return client
