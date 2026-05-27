#!/usr/bin/env python3
"""
Rust Hot Path Integration Tests

Purpose:
    Verify that the Rust hot path components work correctly with Python.
    Tests each component individually and measures performance.

Author: AI-Generated
Created: 2026-01-27
Modified: 2026-01-27

Usage:
    python scripts/test_rust_integration.py

Dependencies:
    - gabagool_rust (maturin develop)

Output:
    - Console output with test results and performance metrics
"""

import sys
import time
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_import():
    """Test that the Rust module can be imported."""
    print("=" * 60)
    print("TEST: Import gabagool_rust module")
    print("=" * 60)

    try:
        import gabagool_rust
        print(f"  PASS: Module imported successfully")
        print(f"  Health: {gabagool_rust.health_check()}")
        return True
    except ImportError as e:
        print(f"  FAIL: Could not import module: {e}")
        print(f"  Hint: Run 'maturin develop' in rust/ directory")
        return False


def test_price_feed_cache():
    """Test PriceFeedCache component."""
    print("\n" + "=" * 60)
    print("TEST: PriceFeedCache")
    print("=" * 60)

    import gabagool_rust

    cache = gabagool_rust.PriceFeedCache()

    # Test update
    start = time.perf_counter()
    cache.update_snapshot("BTC-15MIN", "BTC", 0.47, 50.0, 0.48, 30.0)
    update_time = (time.perf_counter() - start) * 1000

    # Test get
    start = time.perf_counter()
    snap = cache.get_snapshot("BTC-15MIN")
    get_time = (time.perf_counter() - start) * 1000

    print(f"  Update time: {update_time:.3f}ms")
    print(f"  Get time: {get_time:.3f}ms")
    print(f"  Snapshot: {snap}")

    # Verify data
    assert snap is not None, "Snapshot should not be None"
    assert snap['market_id'] == "BTC-15MIN", "Market ID mismatch"
    assert snap['yes_ask'] == "0.47", "YES ask mismatch"
    assert snap['no_ask'] == "0.48", "NO ask mismatch"

    # Test non-existent
    missing = cache.get_snapshot("NONEXISTENT")
    assert missing is None, "Missing snapshot should be None"

    print("  PASS: All assertions passed")
    return True


def test_strategy():
    """Test GabagoolStrategy component."""
    print("\n" + "=" * 60)
    print("TEST: GabagoolStrategy")
    print("=" * 60)

    import gabagool_rust

    strategy = gabagool_rust.GabagoolStrategy(min_margin=0.005)

    # Test profitable opportunity (0.47 + 0.48 = 0.95, margin = 5%)
    start = time.perf_counter()
    opp = strategy.detect_arbitrage("BTC-15MIN", "BTC", 0.47, 0.48, 50.0, 30.0)
    detect_time = (time.perf_counter() - start) * 1000

    print(f"  Detection time: {detect_time:.3f}ms")
    print(f"  Opportunity (0.95 combined): {opp}")

    assert opp is not None, "Should detect opportunity at 0.95"
    assert float(opp['gross_margin']) == 0.05, f"Margin should be 0.05, got {opp['gross_margin']}"

    # Test no opportunity (0.51 + 0.52 = 1.03, no margin)
    start = time.perf_counter()
    no_opp = strategy.detect_arbitrage("ETH-15MIN", "ETH", 0.51, 0.52, 50.0, 30.0)
    detect_time2 = (time.perf_counter() - start) * 1000

    print(f"  Detection time (no opp): {detect_time2:.3f}ms")
    print(f"  Opportunity (1.03 combined): {no_opp}")

    assert no_opp is None, "Should NOT detect opportunity at 1.03"

    # Test edge case (1.00 exactly)
    edge_opp = strategy.detect_arbitrage("SOL-15MIN", "SOL", 0.50, 0.50, 50.0, 30.0)
    assert edge_opp is None, "Should NOT detect opportunity at exactly 1.00"

    print("  PASS: All assertions passed")
    return True


def test_position_tracker():
    """Test PositionTracker component."""
    print("\n" + "=" * 60)
    print("TEST: PositionTracker")
    print("=" * 60)

    import gabagool_rust

    tracker = gabagool_rust.PositionTracker()

    # Add YES position
    start = time.perf_counter()
    tracker.add_yes_position("BTC-15MIN", 30.0, 14.1)  # 30 shares, $14.1 cost
    add_yes_time = (time.perf_counter() - start) * 1000

    # Add NO position
    start = time.perf_counter()
    tracker.add_no_position("BTC-15MIN", 30.0, 14.4)  # 30 shares, $14.4 cost
    add_no_time = (time.perf_counter() - start) * 1000

    # Get position
    start = time.perf_counter()
    pos = tracker.get_position("BTC-15MIN")
    get_time = (time.perf_counter() - start) * 1000

    print(f"  Add YES time: {add_yes_time:.3f}ms")
    print(f"  Add NO time: {add_no_time:.3f}ms")
    print(f"  Get time: {get_time:.3f}ms")
    print(f"  Position: {pos}")

    # Verify
    assert pos is not None, "Position should exist"
    assert float(pos['yes_shares']) == 30.0, "YES shares mismatch"
    assert float(pos['no_shares']) == 30.0, "NO shares mismatch"
    assert float(pos['total_cost']) == 28.5, f"Total cost should be 28.5, got {pos['total_cost']}"
    assert pos['has_complete_pair'] == True, "Should have complete pair"
    assert float(pos['guaranteed_profit']) == 1.5, f"Profit should be 1.5, got {pos['guaranteed_profit']}"

    # Test get all
    all_pos = tracker.get_all_positions()
    assert len(all_pos) == 1, "Should have 1 position"

    print("  PASS: All assertions passed")
    return True


def test_order_executor():
    """Test OrderExecutor component."""
    print("\n" + "=" * 60)
    print("TEST: OrderExecutor (paper mode)")
    print("=" * 60)

    import gabagool_rust

    executor = gabagool_rust.OrderExecutor(paper_mode=True, gas_per_tx=0.003)

    assert executor.is_paper_mode() == True, "Should be in paper mode"

    # Execute single order
    start = time.perf_counter()
    single = executor.execute_single("BTC-15MIN", "YES", 0.47, 100.0)
    single_time = (time.perf_counter() - start) * 1000

    print(f"  Single order time: {single_time:.3f}ms")
    print(f"  Single result: {single}")

    assert single['status'] == 'filled', f"Status should be filled, got {single['status']}"

    # Execute arbitrage (YES + NO in parallel)
    start = time.perf_counter()
    arb = executor.execute_arbitrage("BTC-15MIN", 0.47, 0.48, 30.0)
    arb_time = (time.perf_counter() - start) * 1000

    print(f"  Arbitrage time: {arb_time:.3f}ms")
    print(f"  Arbitrage success: {arb['success']}")
    print(f"  Total time reported: {arb['total_time_ms']}ms")

    assert arb['success'] == True, "Arbitrage should succeed"
    assert arb['yes_result']['status'] == 'filled', "YES should be filled"
    assert arb['no_result']['status'] == 'filled', "NO should be filled"

    print("  PASS: All assertions passed")
    return True


def test_performance_benchmark():
    """Benchmark hot path performance."""
    print("\n" + "=" * 60)
    print("BENCHMARK: Hot Path Performance")
    print("=" * 60)

    import gabagool_rust

    # Create components
    cache = gabagool_rust.PriceFeedCache()
    strategy = gabagool_rust.GabagoolStrategy(min_margin=0.005)
    executor = gabagool_rust.OrderExecutor(paper_mode=True, gas_per_tx=0.003)
    tracker = gabagool_rust.PositionTracker()

    iterations = 100
    total_times = []

    for i in range(iterations):
        start = time.perf_counter()

        # Simulate full hot path
        # 1. Update cache
        cache.update_snapshot(f"BTC-{i}", "BTC", 0.47, 50.0, 0.48, 30.0)

        # 2. Detect opportunity
        opp = strategy.detect_arbitrage(f"BTC-{i}", "BTC", 0.47, 0.48, 50.0, 30.0)

        # 3. Execute if opportunity found
        if opp:
            result = executor.execute_arbitrage(f"BTC-{i}", 0.47, 0.48, 30.0)

            # 4. Track position
            if result['success']:
                tracker.add_yes_position(f"BTC-{i}", 30.0, 14.1)
                tracker.add_no_position(f"BTC-{i}", 30.0, 14.4)

        elapsed = (time.perf_counter() - start) * 1000
        total_times.append(elapsed)

    avg_time = sum(total_times) / len(total_times)
    min_time = min(total_times)
    max_time = max(total_times)
    p95_time = sorted(total_times)[int(0.95 * len(total_times))]

    print(f"  Iterations: {iterations}")
    print(f"  Avg hot path: {avg_time:.2f}ms")
    print(f"  Min hot path: {min_time:.2f}ms")
    print(f"  Max hot path: {max_time:.2f}ms")
    print(f"  P95 hot path: {p95_time:.2f}ms")
    print(f"  Target: <40ms")

    if avg_time < 40:
        print(f"  PASS: Average {avg_time:.2f}ms is under 40ms target")
    else:
        print(f"  WARN: Average {avg_time:.2f}ms exceeds 40ms target")

    return avg_time < 100  # Pass if under 100ms (generous for first run)


def test_integration_with_python():
    """Test integration between Rust and Python data flow."""
    print("\n" + "=" * 60)
    print("TEST: Rust-Python Integration")
    print("=" * 60)

    import gabagool_rust

    # Simulate Python data flow
    market_data = {
        "coin": "BTC",
        "slug": "btc-updown-15m-1769540000",
        "up_ask": 0.47,
        "down_ask": 0.48,
        "up_size": 50.0,
        "down_size": 30.0
    }

    # Create Rust components
    strategy = gabagool_rust.GabagoolStrategy(min_margin=0.005)
    executor = gabagool_rust.OrderExecutor(paper_mode=True, gas_per_tx=0.003)
    tracker = gabagool_rust.PositionTracker()

    # Process with Rust
    start = time.perf_counter()

    opp = strategy.detect_arbitrage(
        market_data["slug"],
        market_data["coin"],
        market_data["up_ask"],
        market_data["down_ask"],
        market_data["up_size"],
        market_data["down_size"]
    )

    if opp:
        # Calculate trade size (Python logic)
        combined = market_data["up_ask"] + market_data["down_ask"]
        trade_size = min(100.0, market_data["up_size"] * market_data["up_ask"] * 2)
        tokens = trade_size / combined

        # Execute with Rust
        result = executor.execute_arbitrage(
            market_data["slug"],
            market_data["up_ask"],
            market_data["down_ask"],
            tokens
        )

        if result['success']:
            # Track with Rust
            tracker.add_yes_position(
                market_data["slug"],
                tokens,
                tokens * market_data["up_ask"]
            )
            tracker.add_no_position(
                market_data["slug"],
                tokens,
                tokens * market_data["down_ask"]
            )

    elapsed = (time.perf_counter() - start) * 1000

    print(f"  Integration time: {elapsed:.2f}ms")

    # Verify final state
    pos = tracker.get_position(market_data["slug"])
    print(f"  Final position: {pos}")

    assert pos is not None, "Should have position"
    assert pos['has_complete_pair'] == True, "Should have complete pair"

    print("  PASS: Integration test passed")
    return True


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  GABAGOOL RUST INTEGRATION TESTS")
    print("=" * 60)

    tests = [
        ("Import", test_import),
        ("PriceFeedCache", test_price_feed_cache),
        ("GabagoolStrategy", test_strategy),
        ("PositionTracker", test_position_tracker),
        ("OrderExecutor", test_order_executor),
        ("Performance", test_performance_benchmark),
        ("Integration", test_integration_with_python),
    ]

    results = []

    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, "PASS" if passed else "FAIL"))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append((name, f"ERROR: {e}"))
            if name == "Import":
                # Can't continue without import
                break

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    passed = 0
    failed = 0

    for name, result in results:
        status = "PASS" if result == "PASS" else "FAIL"
        symbol = "OK" if result == "PASS" else "X"
        print(f"  [{symbol}] {name}: {result}")
        if result == "PASS":
            passed += 1
        else:
            failed += 1

    print("-" * 60)
    print(f"  Total: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
