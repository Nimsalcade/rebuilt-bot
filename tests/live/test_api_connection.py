#!/usr/bin/env python3
"""
Live Test - API Connection

Purpose:
    Test connectivity to Polymarket CLOB and Gamma APIs.
    Run this before deploying to verify infrastructure.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-27

Usage:
    # From gabagool directory with venv activated:
    python tests/live/test_api_connection.py

    # Or run specific tests:
    python tests/live/test_api_connection.py --gamma-only
    python tests/live/test_api_connection.py --clob-only
"""

import asyncio
import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.gamma_client import GammaClient, Market
from src.client import ClobClient, GammaClient as ClobGammaClient
from src.config import Config


def test_gamma_api_connection() -> bool:
    """Test Gamma API connection with market discovery."""
    print("\n" + "=" * 50)
    print("GAMMA API CONNECTION TEST")
    print("=" * 50)

    try:
        client = GammaClient()
        print(f"✓ Client initialized (host: {client.host})")

        # Test 1: Get active 15-minute markets
        print("\n1. Testing 15-minute market discovery...")
        coins = ["BTC", "ETH", "SOL"]
        markets_found = 0

        for coin in coins:
            market = client.get_current_15m_market(coin)
            if market:
                slug = market.get("slug", "unknown")
                accepting = market.get("acceptingOrders", False)
                print(f"   ✓ {coin}: {slug} (accepting: {accepting})")
                markets_found += 1
            else:
                print(f"   ✗ {coin}: No active market found")

        print(f"\n   Found {markets_found}/{len(coins)} active markets")

        # Test 2: Parse market info
        if markets_found > 0:
            print("\n2. Testing market info parsing...")
            for coin in coins:
                info = client.get_market_info(coin)
                if info:
                    token_ids = info.get("token_ids", {})
                    prices = info.get("prices", {})
                    print(f"   ✓ {coin}:")
                    print(f"      Token IDs: up={token_ids.get('up', 'N/A')[:16]}...")
                    print(f"      Prices: up={prices.get('up', 0):.4f}, down={prices.get('down', 0):.4f}")
                    break  # Just test one

        # Test 3: Get all active markets
        print("\n3. Testing get_all_active_markets()...")
        all_markets = client.get_all_active_markets(coins)
        print(f"   ✓ Retrieved {len(all_markets)} Market objects")

        if all_markets:
            m = all_markets[0]
            print(f"   Sample: {m.slug}")
            print(f"      YES token: {m.yes_token_id[:20] if m.yes_token_id else 'N/A'}...")
            print(f"      NO token: {m.no_token_id[:20] if m.no_token_id else 'N/A'}...")
            print(f"      Active: {m.active}, Resolved: {m.resolved}")

        print("\n" + "-" * 50)
        print("GAMMA API: PASS ✓")
        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("\n" + "-" * 50)
        print("GAMMA API: FAIL ✗")
        return False


def test_clob_public_endpoints() -> bool:
    """Test CLOB API public endpoints (no auth required)."""
    print("\n" + "=" * 50)
    print("CLOB API PUBLIC ENDPOINTS TEST")
    print("=" * 50)

    try:
        # First, get a token ID from Gamma API
        gamma = GammaClient()
        btc_market = gamma.get_current_15m_market("BTC")

        if not btc_market:
            print("✗ Cannot test CLOB: No active BTC market found")
            return False

        token_ids = gamma.parse_token_ids(btc_market)
        token_id = token_ids.get("up") or token_ids.get("yes")

        if not token_id:
            print("✗ Cannot test CLOB: No token ID found")
            return False

        print(f"✓ Using token ID: {token_id[:20]}...")

        # Initialize CLOB client (public, no auth)
        client = ClobClient(host="https://clob.polymarket.com")
        print(f"✓ Client initialized (host: {client.host})")

        # Test 1: Get order book
        print("\n1. Testing order book retrieval...")
        try:
            orderbook = client.get_order_book(token_id)
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            print(f"   ✓ Order book retrieved")
            print(f"      Bids: {len(bids)} levels")
            print(f"      Asks: {len(asks)} levels")

            if bids:
                best_bid = bids[0]
                print(f"      Best bid: {best_bid.get('price', 'N/A')} @ {best_bid.get('size', 'N/A')}")
            if asks:
                best_ask = asks[0]
                print(f"      Best ask: {best_ask.get('price', 'N/A')} @ {best_ask.get('size', 'N/A')}")
        except Exception as e:
            print(f"   ✗ Order book failed: {e}")
            return False

        # Test 2: Get price
        print("\n2. Testing price retrieval...")
        try:
            price = client.get_price(token_id, "BUY")
            print(f"   ✓ BUY price: {price:.4f}")

            price_sell = client.get_price(token_id, "SELL")
            print(f"   ✓ SELL price: {price_sell:.4f}")
        except Exception as e:
            print(f"   ✗ Price retrieval failed: {e}")

        # Test 3: Get midpoint
        print("\n3. Testing midpoint retrieval...")
        try:
            midpoint = client.get_midpoint(token_id)
            print(f"   ✓ Midpoint: {midpoint:.4f}")
        except Exception as e:
            print(f"   ✗ Midpoint retrieval failed: {e}")

        # Test 4: Get spread
        print("\n4. Testing spread retrieval...")
        try:
            spread = client.get_spread(token_id)
            print(f"   ✓ Spread: {spread}")
        except Exception as e:
            print(f"   ✗ Spread retrieval failed: {e}")

        print("\n" + "-" * 50)
        print("CLOB PUBLIC ENDPOINTS: PASS ✓")
        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("\n" + "-" * 50)
        print("CLOB PUBLIC ENDPOINTS: FAIL ✗")
        return False


def test_clob_authenticated_endpoints() -> bool:
    """Test CLOB API authenticated endpoints (requires credentials)."""
    print("\n" + "=" * 50)
    print("CLOB API AUTHENTICATED ENDPOINTS TEST")
    print("=" * 50)

    # Check for credentials
    config = Config.from_env()
    private_key = config.get_private_key()
    safe_address = config.safe_address

    if not private_key or not safe_address:
        print("✗ Credentials not configured")
        print("  Set POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS in config/.env")
        print("  (Copy from config/.env.example)")
        print("\n" + "-" * 50)
        print("CLOB AUTHENTICATED: SKIPPED (no credentials)")
        return None  # None indicates skipped, not failed

    try:
        from src.client import create_authenticated_client

        print(f"✓ Safe address: {safe_address[:10]}...")
        print(f"✓ Private key: ***{private_key[-6:]}")

        # Create authenticated client
        print("\n1. Testing API key derivation...")
        client = create_authenticated_client(
            private_key=private_key,
            safe_address=safe_address
        )
        print("   ✓ API credentials derived successfully")

        # Test: Get open orders
        print("\n2. Testing get_open_orders()...")
        try:
            orders = client.get_open_orders()
            print(f"   ✓ Open orders: {len(orders)}")
        except Exception as e:
            print(f"   ✗ Failed: {e}")
            return False

        # Test: Get trades
        print("\n3. Testing get_trades()...")
        try:
            trades = client.get_trades(limit=5)
            print(f"   ✓ Recent trades: {len(trades)}")
            if trades:
                t = trades[0]
                print(f"      Latest: {t.get('side', 'N/A')} @ {t.get('price', 'N/A')}")
        except Exception as e:
            print(f"   ✗ Failed: {e}")

        print("\n" + "-" * 50)
        print("CLOB AUTHENTICATED: PASS ✓")
        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("\n" + "-" * 50)
        print("CLOB AUTHENTICATED: FAIL ✗")
        return False


def test_auto_redeem_connection() -> bool:
    """Test auto-redeem module connection (requires wallet address)."""
    print("\n" + "=" * 50)
    print("AUTO-REDEEM MODULE TEST")
    print("=" * 50)

    config = Config.from_env()
    wallet_address = config.safe_address

    if not wallet_address:
        print("✗ No wallet address configured")
        print("  Set POLY_SAFE_ADDRESS in config/.env")
        print("\n" + "-" * 50)
        print("AUTO-REDEEM: SKIPPED (no wallet)")
        return None

    try:
        from src.auto_redeem import AutoRedeemer

        print(f"✓ Testing with wallet: {wallet_address[:10]}...")

        async def test_async():
            redeemer = AutoRedeemer(
                wallet_address=wallet_address,
                enabled=True
            )
            print("   ✓ AutoRedeemer initialized")

            # Test: Get redeemable positions
            print("\n1. Checking redeemable positions...")
            positions = await redeemer.get_redeemable_positions()
            print(f"   ✓ Redeemable positions: {len(positions)}")

            # Test: Get all positions
            print("\n2. Checking all positions...")
            all_positions = await redeemer.get_all_positions()
            print(f"   ✓ Total positions: {len(all_positions)}")

            # Test: Get wallet value
            print("\n3. Checking wallet value...")
            value = await redeemer.get_wallet_value()
            print(f"   ✓ Wallet value: ${value:.2f}")

            await redeemer.close()
            return True

        result = asyncio.run(test_async())

        print("\n" + "-" * 50)
        print("AUTO-REDEEM: PASS ✓")
        return result

    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("\n" + "-" * 50)
        print("AUTO-REDEEM: FAIL ✗")
        return False


def test_position_merger() -> bool:
    """Test position merger module."""
    print("\n" + "=" * 50)
    print("POSITION MERGER MODULE TEST")
    print("=" * 50)

    try:
        from src.poly_merger import PositionMerger

        # Test with dry_run mode
        merger = PositionMerger(dry_run=True)
        print("✓ PositionMerger initialized (dry_run=True)")

        # Test: can_merge_positions
        print("\n1. Testing can_merge_positions()...")
        can_merge = merger.can_merge_positions(10.0, 8.0)
        print(f"   ✓ YES=10, NO=8: can_merge={can_merge}")

        can_merge2 = merger.can_merge_positions(10.0, 0.005)
        print(f"   ✓ YES=10, NO=0.005: can_merge={can_merge2}")

        # Test: get_mergeable_amount
        print("\n2. Testing get_mergeable_amount()...")
        amount = merger.get_mergeable_amount(10.0, 8.0)
        print(f"   ✓ Mergeable amount: {amount}")

        # Test: estimate_gas_savings
        print("\n3. Testing estimate_gas_savings()...")
        savings = merger.estimate_gas_savings(5.0)
        print(f"   ✓ Separate sell: {savings['separate_sell_gas']:,} gas")
        print(f"   ✓ Merge: {savings['merge_gas']:,} gas")
        print(f"   ✓ Savings: {savings['savings_gas']:,} gas ({savings['savings_percent']:.1%})")

        # Test: should_merge
        print("\n4. Testing should_merge()...")
        should = merger.should_merge(10.0, 8.0, current_gas_price_gwei=30)
        print(f"   ✓ should_merge: {should}")

        # Test: dry run merge
        print("\n5. Testing dry-run merge...")

        async def test_merge():
            result = await merger.merge_positions(
                condition_id="0x" + "ab" * 32,
                amount=5.0,
                is_neg_risk=False
            )
            return result

        result = asyncio.run(test_merge())
        print(f"   ✓ Dry-run result: success={result.success}, merged={result.amount_merged}")

        print("\n" + "-" * 50)
        print("POSITION MERGER: PASS ✓")
        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        print("\n" + "-" * 50)
        print("POSITION MERGER: FAIL ✗")
        return False


def main():
    """Run all live API tests."""
    parser = argparse.ArgumentParser(description="Gabagool Bot - Live API Tests")
    parser.add_argument("--gamma-only", action="store_true", help="Test only Gamma API")
    parser.add_argument("--clob-only", action="store_true", help="Test only CLOB API")
    parser.add_argument("--auth-only", action="store_true", help="Test only authenticated endpoints")
    parser.add_argument("--modules-only", action="store_true", help="Test only auto-redeem and merger")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  GABAGOOL BOT - LIVE API CONNECTION TESTS")
    print("=" * 60)

    results = {}

    # Run tests based on flags
    if args.gamma_only:
        results["Gamma API"] = test_gamma_api_connection()
    elif args.clob_only:
        results["CLOB Public"] = test_clob_public_endpoints()
    elif args.auth_only:
        results["CLOB Auth"] = test_clob_authenticated_endpoints()
    elif args.modules_only:
        results["Auto-Redeem"] = test_auto_redeem_connection()
        results["Position Merger"] = test_position_merger()
    else:
        # Run all tests
        results["Gamma API"] = test_gamma_api_connection()
        results["CLOB Public"] = test_clob_public_endpoints()
        results["CLOB Auth"] = test_clob_authenticated_endpoints()
        results["Auto-Redeem"] = test_auto_redeem_connection()
        results["Position Merger"] = test_position_merger()

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    passed = 0
    failed = 0
    skipped = 0

    for name, result in results.items():
        if result is None:
            status = "SKIPPED"
            skipped += 1
        elif result:
            status = "PASS ✓"
            passed += 1
        else:
            status = "FAIL ✗"
            failed += 1
        print(f"  {name:20} {status}")

    print("\n" + "-" * 60)
    print(f"  Total: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60 + "\n")

    # Return success if no failures
    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
