#!/usr/bin/env python3
"""
Gabagool Bot - Core Trading Bot (CLOB V2)

Purpose:
    High-level trading bot that wraps the official py-clob-client-v2
    to provide a stable interface for order execution, position queries,
    and market data against Polymarket CLOB **V2** (cutover 2026-04-28).

Why py-clob-client-v2:
    Polymarket migrated to CLOB V2 on 2026-04-28. The legacy `py-clob-client`
    package signs orders with EIP-712 domain version "1" against the V1
    Exchange contract; V2 expects domain version "2" against the V2 Exchange
    addresses. Using the V1 SDK against production produces
    `400 order_version_mismatch` on every order submission. The V2 SDK also
    drops `nonce`/`feeRateBps`/`taker` from the signed order and adds
    `timestamp` (ms), `metadata`, `builder` — protocol-side fee model.

L1/L2 auth is unchanged in V2, so previously-derived API keys keep working.

Public API (unchanged for callers):
    bot.connect()
    bot.place_order(token_id, price, size, side, order_type)
    bot.cancel_order(order_id)
    bot.cancel_all_orders()
    bot.get_open_orders(market=None)
    bot.get_trades(token_id=None, limit=100)
    bot.get_order_book(token_id)
    bot.get_price(token_id, side='BUY')
    bot.get_spread(token_id)
    bot.get_markets(active=True, limit=100)
    bot.get_market(condition_id)
    bot.search_markets(query, limit=20)
"""

import logging
import json
import re
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from pathlib import Path

from py_clob_client_v2 import (
    ClobClient as OfficialClobClient,
    ApiCreds,
    OrderArgs,
    OrderType,
    OpenOrderParams,
    TradeParams,
    PartialCreateOrderOptions,
    OrderPayload,
    BalanceAllowanceParams,
    AssetType,
)
from py_clob_client_v2.exceptions import PolyApiException

from .client import GammaClient


# USDC has 6 decimals; balance values from the API are in micro-USDC.
_USDC_DECIMALS = 6
_USDC_MULT = 10 ** _USDC_DECIMALS

# How long to trust a cached balance read before refreshing.
_BALANCE_CACHE_TTL_S = 30.0

# Fee headroom: fees are dynamic per-market but never exceed ~10% in practice.
# We pad the affordability check by this fraction so we don't waste a round-trip
# on an order the server will reject for fee shortfall.
_FEE_BUFFER_FRACTION = 0.10

# Pattern to extract the gross balance (micro-USDC) from API error messages.
# Error format: "balance: 3234041, sum of matched orders: 1914270, ..."
_BALANCE_RE         = re.compile(r"balance:\s*(\d+)")
_MATCHED_ORDERS_RE  = re.compile(r"sum of matched orders:\s*(\d+)")
_ACTIVE_ORDERS_RE   = re.compile(r"sum of active orders:\s*(\d+)")


@dataclass
class BotConfig:
    """Configuration for the trading bot."""
    private_key: str
    safe_address: str
    clob_api_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137
    funder: str = ""
    signature_type: int = 3  # POLY_GNOSIS_SAFE (V2 SignatureType enum: 0=EOA,1=PROXY,2=SAFE,3=1271)
    dry_run: bool = False
    creds_path: str = "data/api_creds.json"
    relayer_api_key: str = ""
    relayer_api_key_address: str = ""
    builder_api_key: str = ""
    builder_api_secret: str = ""
    builder_api_passphrase: str = ""


class TradingBot:
    """High-level trading bot for Polymarket backed by py-clob-client-v2."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.logger = logging.getLogger("TradingBot")

        # Underlying official V2 client. We start at L1 (signer present, no creds);
        # connect() upgrades to L2 by setting ApiCreds.
        self.clob = OfficialClobClient(
            host=config.clob_api_url,
            chain_id=config.chain_id,
            key=config.private_key,
            signature_type=config.signature_type,
            funder=config.safe_address,
        )

        self.gamma = GammaClient(host=config.gamma_api_url)
        
        self.relayer = None
        if config.relayer_api_key and config.relayer_api_key_address:
            try:
                from py_builder_relayer_client.client import RelayClient
                self.relayer = RelayClient(
                    relayer_url="https://relayer-v2.polymarket.com",
                    chain_id=config.chain_id,
                    private_key=config.private_key,
                )
                self.logger.info("Gasless Relayer Client initialized.")
            except ImportError:
                self.logger.warning("py-builder-relayer-client not installed. Gasless relayer unavailable.")

        self._connected = False
        # Cached NET available collateral balance (micro-USDC).
        # IMPORTANT: the CLOB error messages show THREE separate accounting buckets:
        #   balance            — gross USDC balance (does NOT deduct matched fills)
        #   sum of matched orders — fills that have happened but not yet settled
        #   sum of active orders  — resting limit orders still on the book
        # True available = balance - matched_orders - active_orders
        # We track this net figure so _can_afford() gives accurate pre-flight checks.
        self._cached_balance_micro: Optional[int] = None
        self._balance_cached_at: float = 0.0
        # Optimistic credit for freshly-merged USDC that has not yet shown up in
        # an on-chain balance read. MergeEngine increments this so redeployment
        # isn't throttled by the 30s balance cache. Every authoritative balance
        # read (_refresh_balance / _record_balance_from_error) resets it to 0,
        # because the fresh on-chain figure already includes any settled
        # proceeds. Without that reset the accumulator only ever grows and the
        # live balance inflates without bound, driving over-deployment of cash
        # the wallet does not have. (See project context §7.)
        self.pending_merge_proceeds: float = 0.0
        # Suppress repeated "insufficient balance" warnings spam.
        self._last_insufficient_log_at: float = 0.0

        self.logger.info(
            "TradingBot initialized (dry_run=%s, address=%s..., clob=V2)",
            config.dry_run,
            config.safe_address[:10] if config.safe_address else "none",
        )

    # =========================================================================
    # Authentication / lifecycle
    # =========================================================================

    def connect(self) -> bool:
        """Connect to Polymarket: load cached L2 creds or derive fresh ones.

        L1/L2 auth is identical in V2 (per official migration docs), so any
        previously cached creds remain valid. We still validate them once with
        a cheap call and re-derive on failure to handle account changes.
        """
        creds_path = Path(self.config.creds_path)

        cached = self._load_cached_creds(creds_path)
        if cached is not None:
            self.clob.set_api_creds(cached)
            if self._validate_creds():
                self._connected = True
                self.logger.info("Connected using cached credentials")
                return True
            self.logger.warning(
                "Cached credentials failed validation; re-deriving from L1 signature"
            )

        try:
            creds = self.clob.create_or_derive_api_key()
        except Exception as exc:
            self.logger.error("Failed to derive API credentials: %s", exc)
            return False

        if not creds or not creds.api_key:
            self.logger.error("Derived empty credentials")
            return False

        self.clob.set_api_creds(creds)
        self._save_cached_creds(creds_path, creds)
        self._connected = True
        self.logger.info("Connected with freshly derived credentials")
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ creds
    @staticmethod
    def _load_cached_creds(path: Path) -> Optional[ApiCreds]:
        """Load creds from disk. Tolerates both legacy (apiKey/secret/passphrase)
        and SDK (api_key/api_secret/api_passphrase) JSON shapes."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

        api_key = data.get("api_key") or data.get("apiKey", "")
        secret = data.get("api_secret") or data.get("secret", "")
        passphrase = data.get("api_passphrase") or data.get("passphrase", "")

        if not (api_key and secret and passphrase):
            return None

        return ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase)

    @staticmethod
    def _save_cached_creds(path: Path, creds: ApiCreds) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist in legacy shape so older tooling still reads it.
        path.write_text(
            json.dumps(
                {
                    "apiKey": creds.api_key,
                    "secret": creds.api_secret,
                    "passphrase": creds.api_passphrase,
                },
                indent=2,
            )
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _validate_creds(self) -> bool:
        """Cheap L2 check: list API keys."""
        try:
            self.clob.get_api_keys()
            return True
        except Exception:
            return False

    # =========================================================================
    # Balance tracking (collateral / pUSD)
    # =========================================================================

    def _refresh_balance(self) -> Optional[int]:
        """Query the live available collateral balance and cache it.

        Returns the balance in micro-USDC, or None if the lookup failed (in
        which case we leave any prior cache value in place).
        """
        try:
            resp = self.clob.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
        except Exception as exc:
            self.logger.debug("Balance lookup failed: %s", exc)
            return self._cached_balance_micro

        raw = (resp or {}).get("balance")
        try:
            balance = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            balance = None

        if balance is not None:
            self._cached_balance_micro = balance
            self._balance_cached_at = time.monotonic()
            # This live on-chain read already reflects every merge that has
            # settled, so discard the optimistic merge-proceeds accumulator.
            # Keeping it would double-count those proceeds on top of the
            # refreshed balance and inflate the figure that drives sizing.
            self.pending_merge_proceeds = 0.0
        return self._cached_balance_micro

    def _available_balance_micro(self) -> Optional[int]:
        """Return cached balance (refreshing on TTL expiry); None if unknown."""
        if self.config.dry_run:
            return None
        now = time.monotonic()
        if (
            self._cached_balance_micro is None
            or (now - self._balance_cached_at) > _BALANCE_CACHE_TTL_S
        ):
            return self._refresh_balance()
        return self._cached_balance_micro

    def _record_balance_from_error(self, error_message: str) -> None:
        """Extract true net available balance from a CLOB 400 error and cache it.

        The CLOB error body contains three separate accounting fields:

            balance: 3234041                  ← gross USDC (NOT net of fills)
            sum of matched orders: 1914270    ← filled but unsettled, still locked
            sum of active orders:  1063000    ← resting limit orders on the book

        The server rejects when gross - matched - active - new_order < 0.
        Our local cache must track the same net figure so _can_afford() is
        accurate. Previously we stored the gross balance directly, causing us
        to over-commit by up to the full matched-orders amount (was $1.91 in
        the May 18 incident — the primary cause of the 400-error storm).

        Fix: compute net = balance - matched_orders - active_orders and store
        that.  Subsequent placements then correctly deduct from this net figure.
        """
        raw = error_message or ""

        m_bal = _BALANCE_RE.search(raw)
        if not m_bal:
            # Can't parse balance — force a hard API refresh
            error_lower = raw.lower()
            if "balance" in error_lower or "funds" in error_lower:
                self.logger.warning(
                    "Balance regex failed on error — forcing hard refresh: %s",
                    raw[:120],
                )
                self._refresh_balance()
            return

        try:
            gross = int(m_bal.group(1))
        except (TypeError, ValueError):
            self._refresh_balance()
            return

        # Extract the two locked buckets (default 0 if absent — some error
        # shapes omit them when the bucket is empty).
        matched = 0
        active  = 0
        m_matched = _MATCHED_ORDERS_RE.search(raw)
        m_active  = _ACTIVE_ORDERS_RE.search(raw)
        try:
            if m_matched:
                matched = int(m_matched.group(1))
            if m_active:
                active = int(m_active.group(1))
        except (TypeError, ValueError):
            pass

        net = max(0, gross - matched - active)
        self._cached_balance_micro = net
        self._balance_cached_at = time.monotonic()
        # This net figure comes straight from a live server rejection and is
        # authoritative, so reconcile away the optimistic merge credit too —
        # the same double-count guard as _refresh_balance.
        self.pending_merge_proceeds = 0.0

        self.logger.debug(
            "Balance updated from error | gross=$%.4f matched=$%.4f "
            "active=$%.4f → net=$%.4f",
            gross  / _USDC_MULT,
            matched / _USDC_MULT,
            active  / _USDC_MULT,
            net     / _USDC_MULT,
        )

    def _can_afford(self, price: float, size: float) -> bool:
        """True if our cached balance can plausibly cover this order plus fees.

        Conservative: includes a fee buffer (orders that would cross the spread
        get charged a taker fee on top of the principal). When balance is
        unknown we return True (don't block) — the server is the source of truth.
        """
        balance = self._available_balance_micro()
        if balance is None:
            return True

        principal_micro = int(price * size * _USDC_MULT)
        required = int(principal_micro * (1.0 + _FEE_BUFFER_FRACTION))

        if required > balance:
            now = time.monotonic()
            # Throttle the warning so we don't pollute the log.
            if now - self._last_insufficient_log_at > 10.0:
                self.logger.warning(
                    "Skipping order: insufficient balance "
                    "(have $%.4f, order needs ~$%.4f w/ fees). "
                    "Top up the wallet to resume.",
                    balance / _USDC_MULT,
                    required / _USDC_MULT,
                )
                self._last_insufficient_log_at = now
            return False
        return True

    # =========================================================================
    # Order operations
    # =========================================================================

    def place_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str = "BUY",
        order_type: str = "GTC",
    ) -> Optional[Dict[str, Any]]:
        """Place an order. Returns CLOB response dict on success, None on failure.

        The response always includes an ``orderID`` key for backwards
        compatibility with existing callers (maker_loop expects ``orderID``).
        """
        side = side.upper()

        if self.config.dry_run:
            self.logger.info(
                "DRY RUN: Would place %s order: %s @ $%.3f x %.2f",
                side, token_id[:16], price, size,
            )
            return {
                "orderID": "dry_run_" + token_id[:8],
                "status": "SIMULATED",
                "price": price,
                "size": size,
                "side": side,
            }

        # Pre-flight balance check: skip the entire sign/post round-trip when
        # we already know the wallet can't cover this order. Avoids hammering
        # the CLOB with rejected POSTs when the wallet is drained.
        if not self._can_afford(price, size):
            return None

        try:
            signed = self.clob.create_order(
                OrderArgs(
                    token_id=token_id,
                    price=float(price),
                    size=float(size),
                    side=side,
                ),
                # Tick size & neg_risk auto-resolved by the SDK per token.
                options=PartialCreateOrderOptions(),
            )

            response = self.clob.post_order(signed, order_type=self._order_type(order_type))

            order_id = response.get("orderID") or response.get("id") or ""
            response.setdefault("orderID", order_id)

            if response.get("success") is False or not order_id:
                self.logger.error(
                    "Order rejected: %s %s @ $%.3f x %.2f -> %s",
                    side, token_id[:16], price, size, response,
                )
                return None

            # Reservation accounting: deduct what we just locked up so we don't
            # re-attempt the same dollar of headroom multiple times before the
            # next balance refresh.
            principal_micro = int(price * size * _USDC_MULT)
            if self._cached_balance_micro is not None:
                self._cached_balance_micro = max(
                    0, self._cached_balance_micro - principal_micro
                )

            self.logger.info(
                "Order placed: %s %s @ $%.3f x %.2f -> %s",
                side, token_id[:16], price, size, order_id,
            )
            return response

        except PolyApiException as exc:
            # Server is the source of truth for balance — pull the new value
            # out of "balance: <n>" so the next call short-circuits correctly.
            self._record_balance_from_error(str(exc))
            self.logger.error("Order failed (API): %s", exc)
            return None
        except Exception as exc:
            self.logger.error("Unexpected error placing order: %s", exc)
            return None

    @staticmethod
    def _order_type(order_type: str) -> str:
        ot = (order_type or "GTC").upper()
        return ot if ot in ("GTC", "GTD", "FOK", "FAK") else "GTC"

    def cancel_order(self, order_id: str) -> bool:
        if self.config.dry_run:
            self.logger.info("DRY RUN: Would cancel order %s", order_id)
            return True
        try:
            self.clob.cancel_order(OrderPayload(orderID=order_id))
            self.logger.info("Order cancelled: %s", order_id)
            return True
        except Exception as exc:
            self.logger.error("Cancel failed for %s: %s", order_id, exc)
            return False

    def cancel_all_orders(self) -> Dict[str, Any]:
        if self.config.dry_run:
            self.logger.info("DRY RUN: Would cancel all orders")
            return {"canceled": [], "not_canceled": []}
        try:
            result = self.clob.cancel_all() or {"canceled": [], "not_canceled": []}
            self.logger.info("Cancelled %d orders", len(result.get("canceled", [])))
            return result
        except Exception as exc:
            self.logger.error("Cancel all failed: %s", exc)
            return {"canceled": [], "not_canceled": [], "error": str(exc)}

    # =========================================================================
    # Query operations
    # =========================================================================

    def get_open_orders(self, market: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            params = OpenOrderParams(market=market) if market else None
            return self.clob.get_open_orders(params=params) or []
        except Exception as exc:
            self.logger.error("Failed to get orders: %s", exc)
            return []

    def get_trades(
        self,
        token_id: Optional[str] = None,
        limit: int = 100,  # kept for API compatibility; SDK paginates
    ) -> List[Dict[str, Any]]:
        try:
            params = TradeParams(asset_id=token_id) if token_id else None
            return self.clob.get_trades(params=params) or []
        except Exception as exc:
            self.logger.error("Failed to get trades: %s", exc)
            return []

    def get_order_book(self, token_id: str) -> Dict[str, Any]:
        try:
            book = self.clob.get_order_book(token_id)
            if book is None:
                return {"bids": [], "asks": []}
            bids, asks = self._extract_book_levels(book)
            return {
                "bids": [{"price": p, "size": s} for p, s in bids],
                "asks": [{"price": p, "size": s} for p, s in asks],
                "asset_id": self._get_book_field(book, "asset_id"),
                "tick_size": self._get_book_field(book, "tick_size"),
            }
        except Exception as exc:
            self.logger.error("Failed to get orderbook: %s", exc)
            return {"bids": [], "asks": []}

    def get_price(self, token_id: str, side: str = "BUY") -> float:
        try:
            data = self.clob.get_price(token_id, side.upper())
            if isinstance(data, dict):
                return float(data.get("price", 0))
            return float(data or 0)
        except Exception as exc:
            self.logger.error("Failed to get price: %s", exc)
            return 0.0

    def get_spread(self, token_id: str) -> Dict[str, float]:
        """Return {'bid', 'ask', 'spread'} from the order book.

        Polymarket returns bids ascending and asks descending — so the *best*
        prices are at the END of each list, not the beginning. Raises ValueError
        on 404 so callers (e.g. maker_loop) can drop that market.

        The V2 SDK returns the book as a plain dict (not the V1 OrderBookSummary
        dataclass), so we normalize via _extract_book_levels.
        """
        try:
            book = self.clob.get_order_book(token_id)
            bids, asks = self._extract_book_levels(book)

            best_bid = bids[-1][0] if bids else 0.0
            best_ask = asks[-1][0] if asks else 1.0

            return {"bid": best_bid, "ask": best_ask, "spread": best_ask - best_bid}

        except PolyApiException as exc:
            msg = str(exc)
            if "404" in msg or "not found" in msg.lower():
                raise ValueError(f"Market Not Found: {exc}")
            self.logger.debug("Failed to get spread for %s: %s", token_id, exc)
            return {"bid": 0.0, "ask": 1.0, "spread": 1.0}
        except Exception as exc:
            self.logger.debug("Failed to get spread for %s: %s", token_id, exc)
            return {"bid": 0.0, "ask": 1.0, "spread": 1.0}

    @staticmethod
    def _extract_book_levels(book: Any):
        """Normalize an order book into (bids, asks) where each is a list of
        (price, size) float tuples, regardless of whether `book` came back as
        a V1 OrderBookSummary dataclass or a V2 dict-of-dicts."""
        if book is None:
            return [], []

        def levels(raw):
            out = []
            for entry in (raw or []):
                if isinstance(entry, dict):
                    p, s = entry.get("price"), entry.get("size")
                else:
                    p, s = getattr(entry, "price", None), getattr(entry, "size", None)
                if p is None or s is None:
                    continue
                try:
                    out.append((float(p), float(s)))
                except (TypeError, ValueError):
                    continue
            return out

        if isinstance(book, dict):
            return levels(book.get("bids")), levels(book.get("asks"))
        return levels(getattr(book, "bids", None)), levels(getattr(book, "asks", None))

    @staticmethod
    def _get_book_field(book: Any, field: str) -> Any:
        if isinstance(book, dict):
            return book.get(field)
        return getattr(book, field, None)

    # =========================================================================
    # Market discovery (Gamma API)
    # =========================================================================

    def get_markets(
        self, active: bool = True, limit: int = 100
    ) -> List[Dict[str, Any]]:
        try:
            return self.gamma.get_markets(active=active, limit=limit)
        except Exception as exc:
            self.logger.error("Failed to get markets: %s", exc)
            return []

    def get_market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.gamma.get_market(condition_id)
        except Exception as exc:
            self.logger.error("Failed to get market %s: %s", condition_id, exc)
            return None

    def search_markets(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            return self.gamma.search_markets(query, limit=limit)
        except Exception as exc:
            self.logger.error("Market search failed: %s", exc)
            return []

    # =========================================================================
    # Utility
    # =========================================================================

    def check_prices(
        self, yes_token_id: str, no_token_id: str
    ) -> Dict[str, float]:
        yes_price = self.get_price(yes_token_id, "BUY")
        no_price = self.get_price(no_token_id, "BUY")
        combined = yes_price + no_price
        profit = 1.0 - combined if combined < 1.0 else 0.0

        return {
            "yes_price": yes_price,
            "no_price": no_price,
            "combined_cost": combined,
            "profit_margin": profit,
            "is_arbitrage": combined < 1.0,
        }

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        mode = "DRY RUN" if self.config.dry_run else "LIVE"
        return f"TradingBot({status}, {mode}, V2)"


def create_bot_from_config(config: "Config") -> TradingBot:
    """Create TradingBot from a Config object."""
    private_key = config.get_private_key()
    if not private_key:
        raise ValueError("POLY_PRIVATE_KEY not set in environment")

    bot_config = BotConfig(
        private_key=private_key,
        safe_address=config.safe_address,
        clob_api_url=config.clob.host,
        chain_id=config.clob.chain_id,
        signature_type=config.clob.signature_type,
        dry_run=config.dry_run,
        creds_path=str(Path(config.data_dir) / "api_creds.json"),
        relayer_api_key=config.relayer_api_key,
        relayer_api_key_address=config.relayer_api_key_address,
        builder_api_key=getattr(config, 'builder_api_key', ''),
        builder_api_secret=getattr(config, 'builder_api_secret', ''),
        builder_api_passphrase=getattr(config, 'builder_api_passphrase', ''),
    )
    return TradingBot(bot_config)
