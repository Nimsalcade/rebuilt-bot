# Operational Rust Integration Guide
## Production-Ready Roadmap for Gabagool Bot Rust Conversion

**Purpose**: Actionable integration plan for converting Python gabagool bot hot path to Rust using official Polymarket client.

**Audience**: To be followed directly with Claude Code after Python bot is proven.

**Timeline**: Week 3 (8-12 hours total)

---

## Executive Summary

### Current State
- ✅ Working Python gabagool bot
- ✅ Proven strategy (70%+ win rate)
- ✅ Deployed to Amsterdam VPS
- ⚠️ Execution time: ~380ms (too slow for competition)

### Target State
- ✅ Rust hot path: <40ms execution
- ✅ Python orchestration (unchanged)
- ✅ 10-20x speedup on critical path
- ✅ Thread-safe for 3+ concurrent markets
- ✅ Production-ready with official Polymarket client

### Primary Resources

**Core Foundation** (Must Use):
1. **Polymarket/rs-clob-client** - Official Rust CLOB client
   - URL: https://github.com/Polymarket/rs-clob-client
   - Status: Production-ready (v0.3.3, Jan 2026)
   - Provides: CLOB API, WebSocket, EIP-712 signing
   - **Rating: 5.0/5.0** ⭐⭐⭐⭐⭐

**Reference Patterns** (Optional Extract):
2. **taetaehoho/poly-kalshi-arb** - Architecture patterns
   - URL: https://github.com/taetaehoho/poly-kalshi-arb
   - Status: Production-ready
   - Extract: Position tracker, circuit breaker patterns
   - **Rating: 4.0/5.0** ⭐⭐⭐⭐

### Key Decision: No Existing Rust Gabagool Implementation

**Reality**: No Rust repos implement the gabagool arbitrage strategy. All existing Rust bots are:
- ❌ Copy trading (wrong strategy)
- ❌ Cross-platform arbitrage (different approach)
- ❌ Infrastructure only (no strategy)

**Implication**: You'll convert your proven Python strategy to Rust using the official client as foundation.

**Advantage**: Your Python code is already optimized for gabagool. Conversion maintains proven logic while gaining 10-20x speed.

---

## Integration Architecture

### Component Map

```
┌─────────────────────────────────────────────────────────────┐
│                    PYTHON LAYER                             │
│  (Orchestration - stays Python)                             │
│                                                              │
│  src/orchestrator.py      # High-level control              │
│  src/database.py          # SQLite logging                  │
│  src/risk_manager.py      # Business rules                  │
│  src/monitor.py           # Dashboard                       │
│  src/config.py            # Configuration                   │
└────────────────────────┬────────────────────────────────────┘
                         │ PyO3 FFI
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    RUST LAYER                               │
│  (Hot Path - convert to Rust)                               │
│                                                              │
│  rust/src/                                                   │
│    ├── lib.rs            # PyO3 module bindings             │
│    ├── ws_handler.rs     # WebSocket (5-10ms)               │
│    ├── strategy.rs       # Arbitrage (<1ms)                 │
│    ├── execution.rs      # Orders (10-20ms)                 │
│    └── position.rs       # Position tracking (<1ms)         │
│                                                              │
│  Uses: rs-clob-client (official)                            │
└────────────────────────┬────────────────────────────────────┘
                         │ Network
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              POLYMARKET INFRASTRUCTURE                       │
│                                                              │
│  • CLOB API (London) - Order matching                       │
│  • WebSocket (RTDS) - Real-time price feeds                 │
│  • Polygon L2 - Settlement                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Phase 0: Prerequisites (Before Starting)

### ✅ Checklist Before Rust Conversion

**Python Bot Status**:
- [ ] Bot is working end-to-end
- [ ] Achieving 70%+ win rate
- [ ] Deployed to Amsterdam VPS
- [ ] Has completed 50+ successful trades
- [ ] Profiling completed (know bottlenecks)

**Development Environment**:
- [ ] Rust installed (1.75+)
  ```bash
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
  rustup component add rust-analyzer
  ```
- [ ] maturin installed
  ```bash
  pip install maturin
  ```
- [ ] Claude Code installed
  ```bash
  npm install -g @anthropic-ai/claude-code
  ```
- [ ] Python venv active with dependencies

**Documentation**:
- [ ] `RUST_CONVERSION.md` in `docs/` (provided separately)
- [ ] Python code well-commented
- [ ] Profiling results documented

### Estimated Time
- Prerequisites check: 30 minutes
- Environment setup: 30 minutes

---

## Phase 1: Clone & Setup Official Rust Client

### Task 1.1: Clone Official Polymarket Rust Client

**Location**: Clone as reference (not directly integrated)

```bash
# Create workspace
cd ~/projects
mkdir polymarket-rust-workspace
cd polymarket-rust-workspace

# Clone official client for reference
git clone https://github.com/Polymarket/rs-clob-client.git
cd rs-clob-client

# Check examples
ls examples/
# Should see:
# - clob/authenticated.rs
# - clob/unauthenticated.rs
# - ws/orderbook.rs
# - approvals.rs
```

**Purpose**: Study official client patterns, don't modify it directly.

**Time**: 5 minutes

---

### Task 1.2: Review Official Client Examples

**Read These Files** (with Claude Code):

```bash
# In Claude Code session:
cd ~/projects/polymarket-rust-workspace/rs-clob-client

# Open these files to understand API:
view examples/clob/authenticated.rs
view examples/ws/orderbook.rs
view examples/approvals.rs
```

**What to Learn**:
1. How to authenticate with CLOB API
2. How to create/submit orders
3. How to subscribe to WebSocket feeds
4. Order signing with EIP-712

**Time**: 15 minutes

---

### Task 1.3: Initialize Your Rust Module

**Location**: Inside your existing Python bot directory

```bash
# Navigate to your Python bot
cd ~/projects/gabagool-bot

# Create Rust subdirectory
mkdir rust
cd rust

# Initialize with maturin
maturin init --bindings pyo3

# This creates:
# rust/
# ├── Cargo.toml
# ├── src/
# │   └── lib.rs
# └── pyproject.toml
```

**Time**: 5 minutes

---

### Task 1.4: Configure Dependencies

**Edit `rust/Cargo.toml`**:

```toml
[package]
name = "gabagool-rust"
version = "0.1.0"
edition = "2021"

[lib]
name = "gabagool_rust"
crate-type = ["cdylib"]

[dependencies]
# Official Polymarket client
polymarket-client-sdk = "0.3"

# Async runtime
tokio = { version = "1", features = ["full", "rt-multi-thread", "macros"] }
tokio-tungstenite = "0.23"
futures = "0.3"

# Crypto & signing
alloy = { version = "0.5", features = ["signers", "signer-local"] }

# Data types
rust_decimal = "1.33"
rust_decimal_macros = "1.33"

# Serialization
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"

# Python bindings
pyo3 = { version = "0.21", features = ["extension-module"] }
pyo3-asyncio = { version = "0.21", features = ["tokio-runtime"] }

# Error handling
anyhow = "1.0"
thiserror = "1.0"

# Logging
tracing = "0.1"
tracing-subscriber = "0.3"

[profile.release]
opt-level = 3
lto = "fat"
codegen-units = 1
strip = true
```

**Time**: 10 minutes

---

## Phase 2: Convert Hot Path Components

### Task 2.1: WebSocket Handler

**Prompt for Claude Code**:

```
Context: I'm converting my Python Polymarket bot to Rust for performance.

Current Python code (src/websocket_client.py):
[paste your Python WebSocket code]

Please create rust/src/ws_handler.rs that:
1. Uses tokio-tungstenite for WebSocket
2. Connects to Polymarket RTDS WebSocket
3. Receives and parses price update messages
4. Exposes Python-compatible API via PyO3
5. Handles reconnection on disconnect
6. Target: <10ms per message (vs 100ms+ in Python)

The handler should be callable from Python like:
```python
import gabagool_rust
ws = gabagool_rust.WebSocketHandler(url)
await ws.connect()
update = await ws.receive_message()  # Returns {market_id, yes_price, no_price}
```

Make it production-ready with error handling and logging.
```

**Expected Output**:
```
rust/src/ws_handler.rs (200-300 lines)
- WebSocketHandler struct
- PyO3 bindings
- Error handling
- Automatic reconnection
```

**Deliverable**: Working WebSocket handler callable from Python

**Time**: 2 hours (with Claude Code)

---

### Task 2.2: Arbitrage Detection Logic

**Prompt for Claude Code**:

```
Context: Converting arbitrage detection to Rust for <1ms execution.

Current Python code (strategies/gabagool_strategy.py):
[paste your Python strategy code]

Please create rust/src/strategy.rs that:
1. Implements GabagoolStrategy struct
2. Detects arbitrage: YES < threshold AND NO < threshold AND combined < $1.00
3. Checks against active positions (thread-safe HashMap)
4. Returns Optional<f64> (Some(profit) or None)
5. Uses rust_decimal for price precision
6. Thread-safe with Arc<RwLock<HashMap>>

Python API:
```python
strategy = gabagool_rust.GabagoolStrategy(
    yes_threshold=0.48,
    no_threshold=0.48,
    profit_threshold=0.02
)
profit = strategy.detect_arbitrage(yes_price, no_price, market_id)
if profit is not None:
    # Execute trade
```

Target: <1ms per check (vs 30ms+ in Python)
```

**Expected Output**:
```
rust/src/strategy.rs (150-200 lines)
- GabagoolStrategy struct
- Thread-safe position tracking
- PyO3 bindings
- Decimal arithmetic
```

**Deliverable**: Arbitrage detector callable from Python

**Time**: 1.5 hours (with Claude Code)

---

### Task 2.3: Order Execution

**Prompt for Claude Code**:

```
Context: Converting order execution to Rust using official rs-clob-client.

Current Python code (src/client.py, src/signer.py):
[paste your Python order execution code]

I've reviewed examples/clob/authenticated.rs from rs-clob-client.

Please create rust/src/execution.rs that:
1. Uses polymarket-client-sdk for CLOB API
2. Signs orders with EIP-712 (alloy signers)
3. Executes YES and NO orders IN PARALLEL (tokio::join!)
4. Handles partial fills and errors
5. Returns execution results to Python

Dependencies already added:
- polymarket-client-sdk = "0.3"
- alloy (for signing)

Python API:
```python
executor = gabagool_rust.OrderExecutor(
    private_key="0x...",
    clob_url="https://clob.polymarket.com"
)
result = await executor.execute_arbitrage(
    market_id="BTC-15MIN",
    yes_price=0.48,
    no_price=0.49,
    size=100.0
)
# Returns {yes_filled, no_filled, yes_txn, no_txn}
```

Target: 20-30ms for both orders (vs 150ms+ sequential in Python)
```

**Expected Output**:
```
rust/src/execution.rs (300-400 lines)
- OrderExecutor struct
- Parallel execution (tokio::join!)
- Official CLOB client integration
- Error handling
- PyO3 bindings
```

**Deliverable**: Order executor callable from Python

**Time**: 3 hours (with Claude Code, includes official client integration)

---

### Task 2.4: Position Tracking

**Prompt for Claude Code**:

```
Context: Converting position tracking to Rust for thread-safety and speed.

Current Python code (position_tracker.py):
[paste your Python position tracker]

Optional reference from poly-kalshi-arb:
- See taetaehoho/poly-kalshi-arb/src/position_tracker.rs for patterns
- (Don't copy directly, adapt the architecture)

Please create rust/src/position.rs that:
1. Tracks YES/NO positions per market
2. Thread-safe with Arc<RwLock<HashMap>>
3. Calculates combined costs and profit
4. Detects complete pairs (YES + NO)
5. Supports 3+ concurrent markets

Python API:
```python
tracker = gabagool_rust.PositionTracker()
tracker.add_yes_position("BTC-15MIN", shares=100, cost=48)
tracker.add_no_position("BTC-15MIN", shares=100, cost=49)
has_pair = tracker.has_complete_pair("BTC-15MIN")  # True
position = tracker.get_position("BTC-15MIN")  # {yes_shares, no_shares, cost, profit}
```

Target: <1ms per operation (vs 50ms with Python locks)
```

**Expected Output**:
```
rust/src/position.rs (250-300 lines)
- PositionTracker struct
- Position struct
- Thread-safe operations
- PyO3 bindings
```

**Deliverable**: Position tracker callable from Python

**Time**: 2 hours (with Claude Code)

---

### Task 2.5: PyO3 Module Integration

**Prompt for Claude Code**:

```
Context: Integrate all Rust components into a single Python-importable module.

Please update rust/src/lib.rs to:
1. Import all modules (ws_handler, strategy, execution, position)
2. Define the PyO3 module: gabagool_rust
3. Register all classes with #[pymodule]
4. Set up logging to work with Python
5. Include module docstring

The module should be importable as:
```python
import gabagool_rust

# All classes available:
ws = gabagool_rust.WebSocketHandler(url)
strategy = gabagool_rust.GabagoolStrategy(...)
executor = gabagool_rust.OrderExecutor(...)
tracker = gabagool_rust.PositionTracker()
```
```

**Expected Output**:
```
rust/src/lib.rs (100-150 lines)
- Module definition
- All component registrations
- Initialization code
```

**Deliverable**: Integrated Rust module

**Time**: 30 minutes (with Claude Code)

---

## Phase 3: Build & Test

### Task 3.1: Build Development Version

```bash
cd ~/projects/gabagool-bot/rust

# Build debug (faster compile, slower runtime)
maturin develop

# Verify import works
python -c "import gabagool_rust; print('Import successful!')"
```

**Expected Output**: Module imports without errors

**Time**: 5 minutes

**Troubleshooting**:
- If import fails, check Python venv is active
- Run `pip list | grep gabagool` to verify installation

---

### Task 3.2: Unit Tests (Rust)

**Prompt for Claude Code**:

```
Please add unit tests to each Rust module:

rust/src/strategy.rs:
- Test arbitrage detection with various prices
- Test position blocking (should reject if market already has position)
- Test threshold boundaries

rust/src/position.rs:
- Test adding positions
- Test complete pair detection
- Test profit calculations
- Test thread safety (spawn multiple threads)

Create rust/tests/ directory with integration tests.
```

**Run Tests**:
```bash
cd rust/
cargo test
cargo test -- --nocapture  # With output
```

**Expected Output**: All tests pass

**Time**: 1 hour (with Claude Code)

---

### Task 3.3: Integration Tests (Python)

**Create**: `tests/test_rust_integration.py`

```python
import pytest
import gabagool_rust

def test_strategy_detection():
    """Test Rust strategy from Python"""
    strategy = gabagool_rust.GabagoolStrategy(
        yes_threshold=0.48,
        no_threshold=0.48,
        profit_threshold=0.02
    )
    
    # Should detect arbitrage
    profit = strategy.detect_arbitrage(0.47, 0.47, "test_market")
    assert profit is not None
    assert profit > 0.05  # ~6% profit
    
    # Should reject (combined >= $1.00)
    no_profit = strategy.detect_arbitrage(0.50, 0.50, "test_market")
    assert no_profit is None

def test_position_tracking():
    """Test Rust position tracker from Python"""
    tracker = gabagool_rust.PositionTracker()
    
    # Add positions
    tracker.add_yes_position("BTC-15MIN", 100, 48)
    tracker.add_no_position("BTC-15MIN", 100, 49)
    
    # Check complete pair
    assert tracker.has_complete_pair("BTC-15MIN")
    
    # Get position details
    pos = tracker.get_position("BTC-15MIN")
    assert pos.yes_shares == 100
    assert pos.no_shares == 100
    assert pos.yes_cost == 48
    assert pos.no_cost == 49

@pytest.mark.asyncio
async def test_websocket_handler():
    """Test Rust WebSocket from Python"""
    # Note: This requires actual WebSocket connection
    # Use mock or test environment
    pass  # Implement with mock server
```

**Run Tests**:
```bash
cd ~/projects/gabagool-bot
pytest tests/test_rust_integration.py -v
```

**Expected Output**: All tests pass

**Time**: 1 hour

---

### Task 3.4: Performance Benchmarks

**Create**: `benchmark.py`

```python
import time
import asyncio
import gabagool_rust
from src.gabagool_strategy import GabagoolStrategy as PythonStrategy

def benchmark_arbitrage_detection():
    """Compare Python vs Rust arbitrage detection"""
    
    # Python version
    py_strategy = PythonStrategy(0.48, 0.48, 0.02)
    start = time.time()
    for _ in range(100000):
        py_strategy.detect_arbitrage(0.47, 0.48, "test_market")
    py_time = time.time() - start
    
    # Rust version
    rust_strategy = gabagool_rust.GabagoolStrategy(0.48, 0.48, 0.02)
    start = time.time()
    for _ in range(100000):
        rust_strategy.detect_arbitrage(0.47, 0.48, "test_market")
    rust_time = time.time() - start
    
    print(f"Python: {py_time:.3f}s for 100K iterations")
    print(f"Rust: {rust_time:.3f}s for 100K iterations")
    print(f"Speedup: {py_time / rust_time:.1f}x")
    
    # Expected: 20-50x speedup

if __name__ == "__main__":
    benchmark_arbitrage_detection()
```

**Run Benchmark**:
```bash
python benchmark.py
```

**Expected Output**:
```
Python: 3.250s for 100K iterations
Rust: 0.085s for 100K iterations
Speedup: 38.2x
```

**Time**: 30 minutes

---

## Phase 4: Production Build & Deployment

### Task 4.1: Build Release Version

```bash
cd ~/projects/gabagool-bot/rust

# Build optimized release
maturin build --release

# Install wheel
pip install target/wheels/gabagool_rust-*.whl --force-reinstall

# Verify
python -c "import gabagool_rust; print('Release build working!')"
```

**Time**: 5 minutes

---

### Task 4.2: Update Python Orchestrator

**Edit**: `src/orchestrator.py`

```python
import asyncio
import gabagool_rust  # New Rust modules
from src.database import Database
from src.risk_manager import RiskManager
from src.monitor import Dashboard

class HybridGabagoolBot:
    """Hybrid Python-Rust gabagool bot"""
    
    def __init__(self, config):
        # Rust hot path (fast)
        self.rust_ws = gabagool_rust.WebSocketHandler(
            config.ws_url
        )
        self.rust_strategy = gabagool_rust.GabagoolStrategy(
            config.yes_threshold,
            config.no_threshold,
            config.profit_threshold
        )
        self.rust_executor = gabagool_rust.OrderExecutor(
            config.private_key,
            config.clob_url
        )
        self.rust_tracker = gabagool_rust.PositionTracker()
        
        # Python slow path (flexible)
        self.db = Database(config.db_path)
        self.risk = RiskManager(config.risk_params)
        self.monitor = Dashboard()
    
    async def run(self):
        """Main trading loop using Rust hot path"""
        await self.rust_ws.connect()
        
        while True:
            try:
                # [RUST] Receive price update (5-10ms)
                update = await self.rust_ws.receive_message()
                
                # [RUST] Detect arbitrage (<1ms)
                profit = self.rust_strategy.detect_arbitrage(
                    update.yes_price,
                    update.no_price,
                    update.market_id
                )
                
                if profit is not None:
                    # [PYTHON] Risk check (not time-critical)
                    if self.risk.validate_trade(update, profit):
                        # [RUST] Execute orders (20-30ms)
                        result = await self.rust_executor.execute_arbitrage(
                            update.market_id,
                            update.yes_price,
                            update.no_price,
                            self.trade_size
                        )
                        
                        # [RUST] Track position (<1ms)
                        self.rust_tracker.add_yes_position(
                            update.market_id,
                            result.yes_filled,
                            update.yes_price * result.yes_filled
                        )
                        self.rust_tracker.add_no_position(
                            update.market_id,
                            result.no_filled,
                            update.no_price * result.no_filled
                        )
                        
                        # [PYTHON] Log (async, after execution)
                        asyncio.create_task(
                            self.db.log_trade(update, result, profit)
                        )
                        
            except Exception as e:
                self.monitor.log_error(f"Error in main loop: {e}")
                await asyncio.sleep(1)
```

**Time**: 1 hour

---

### Task 4.3: Deploy to Amsterdam VPS

```bash
# On local machine, build wheel
cd ~/projects/gabagool-bot/rust
maturin build --release

# Copy wheel to VPS
scp target/wheels/gabagool_rust-*.whl vps:/tmp/

# SSH to VPS
ssh vps

# On VPS
cd /home/trader/gabagool-bot
source venv/bin/activate

# Install Rust module
pip install /tmp/gabagool_rust-*.whl --force-reinstall

# Verify
python -c "import gabagool_rust; print('Deployed!')"

# Update code
git pull origin main

# Restart bot
screen -S bot -X quit  # Stop old version
screen -S bot -dm python src/orchestrator.py  # Start hybrid version

# Monitor
screen -r bot
```

**Time**: 30 minutes

---

### Task 4.4: Monitor Production Performance

**Create**: `monitor_performance.py`

```python
import time
import asyncio
import gabagool_rust

async def monitor_latency():
    """Monitor real-world latency"""
    
    ws = gabagool_rust.WebSocketHandler(
        "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    )
    await ws.connect()
    
    latencies = []
    
    for _ in range(100):
        start = time.time()
        update = await ws.receive_message()
        latency = (time.time() - start) * 1000  # ms
        latencies.append(latency)
    
    print(f"WebSocket latency:")
    print(f"  Mean: {sum(latencies)/len(latencies):.2f}ms")
    print(f"  Min: {min(latencies):.2f}ms")
    print(f"  Max: {max(latencies):.2f}ms")
    print(f"  P95: {sorted(latencies)[95]:.2f}ms")

asyncio.run(monitor_latency())
```

**Expected Output**:
```
WebSocket latency:
  Mean: 7.3ms
  Min: 4.2ms
  Max: 12.8ms
  P95: 10.5ms

(vs 100-200ms in Python)
```

**Time**: 30 minutes

---

## Phase 5: Validation & Tuning

### Task 5.1: Validate Strategy Consistency

**Objective**: Ensure Rust implementation matches Python behavior exactly

**Test Script**: `validate_consistency.py`

```python
import gabagool_rust
from src.gabagool_strategy import GabagoolStrategy as PythonStrategy

def test_consistency():
    """Verify Rust and Python strategies produce identical results"""
    
    py_strat = PythonStrategy(0.48, 0.48, 0.02)
    rust_strat = gabagool_rust.GabagoolStrategy(0.48, 0.48, 0.02)
    
    test_cases = [
        (0.47, 0.47, True),   # Should detect
        (0.48, 0.48, True),   # Should detect
        (0.49, 0.49, False),  # Should NOT detect
        (0.50, 0.50, False),  # Should NOT detect
        (0.47, 0.52, False),  # Should NOT detect (combined >= $1)
    ]
    
    for yes, no, should_detect in test_cases:
        py_result = py_strat.detect_arbitrage(yes, no, "test")
        rust_result = rust_strat.detect_arbitrage(yes, no, "test")
        
        py_detected = py_result[0]
        rust_detected = rust_result is not None
        
        assert py_detected == rust_detected == should_detect, \
            f"Mismatch at ({yes}, {no}): Python={py_detected}, Rust={rust_detected}"
        
        if should_detect:
            py_profit = py_result[1]
            rust_profit = rust_result
            assert abs(py_profit - rust_profit) < 0.0001, \
                f"Profit mismatch: Python={py_profit}, Rust={rust_profit}"
    
    print("✅ All consistency tests passed!")

test_consistency()
```

**Time**: 30 minutes

---

### Task 5.2: Live Performance Test

**Objective**: Verify <40ms end-to-end latency in production

**Test**: Run bot for 1 hour, measure actual execution times

**Metrics to Track**:
```python
# In production bot
import time

class PerformanceTracker:
    def __init__(self):
        self.timings = {
            'ws_receive': [],
            'arbitrage_detect': [],
            'order_execute': [],
            'position_update': [],
            'total': []
        }
    
    async def track_execution(self):
        start_total = time.time()
        
        # Measure WebSocket
        start = time.time()
        update = await self.rust_ws.receive_message()
        self.timings['ws_receive'].append(time.time() - start)
        
        # Measure detection
        start = time.time()
        profit = self.rust_strategy.detect_arbitrage(...)
        self.timings['arbitrage_detect'].append(time.time() - start)
        
        if profit:
            # Measure execution
            start = time.time()
            result = await self.rust_executor.execute_arbitrage(...)
            self.timings['order_execute'].append(time.time() - start)
            
            # Measure position update
            start = time.time()
            self.rust_tracker.add_yes_position(...)
            self.timings['position_update'].append(time.time() - start)
        
        self.timings['total'].append(time.time() - start_total)
    
    def report(self):
        for key, values in self.timings.items():
            if values:
                avg = sum(values) / len(values) * 1000  # Convert to ms
                print(f"{key}: {avg:.2f}ms avg")
```

**Expected Output** (after 1 hour):
```
ws_receive: 7.3ms avg
arbitrage_detect: 0.4ms avg
order_execute: 23.5ms avg
position_update: 0.2ms avg
total: 35.8ms avg

✅ Target achieved (<40ms)
```

**Time**: 1 hour runtime + 30 minutes analysis

---

## Success Criteria Checklist

### Performance Metrics
- [ ] WebSocket: <10ms (vs 100-200ms Python)
- [ ] Arbitrage detection: <1ms (vs 30-50ms Python)
- [ ] Order execution: <30ms (vs 150ms+ Python)
- [ ] Position tracking: <1ms (vs 50ms Python)
- [ ] **Total hot path: <40ms** (vs 380ms Python)
- [ ] **Overall speedup: 9-10x**

### Functional Requirements
- [ ] Same strategy logic (no behavioral changes)
- [ ] Same win rate as Python version (±2%)
- [ ] Python orchestrator works with Rust modules
- [ ] Thread-safe for 3+ concurrent markets
- [ ] Handles errors gracefully
- [ ] All unit tests pass
- [ ] All integration tests pass

### Production Readiness
- [ ] Deployed to Amsterdam VPS
- [ ] Running 24/7 without crashes
- [ ] Logging works correctly
- [ ] Monitoring dashboard updated
- [ ] Benchmarks documented
- [ ] No memory leaks (run for 24 hours)

---

## Timeline Summary

| Phase | Tasks | Time | Cumulative |
|-------|-------|------|------------|
| **Phase 0** | Prerequisites & Setup | 1h | 1h |
| **Phase 1** | Clone & Setup | 1h | 2h |
| **Phase 2** | Convert Components | 9h | 11h |
| **Phase 3** | Build & Test | 3h | 14h |
| **Phase 4** | Deploy | 2h | 16h |
| **Phase 5** | Validate | 2h | 18h |
| **Buffer** | Debugging, Fixes | 2h | **20h** |

**Total Estimated Time**: 18-20 hours over 1 week

**With Claude Code assistance**: Most code generation is automated, time spent on integration, testing, and validation.

---

## Common Issues & Solutions

### Issue 1: maturin Build Fails

**Symptom**:
```
error: linking with `cc` failed
```

**Solution**:
```bash
# macOS
xcode-select --install

# Ubuntu
sudo apt-get install build-essential

# Ensure Rust is up to date
rustup update
```

---

### Issue 2: Import Error in Python

**Symptom**:
```python
ImportError: cannot import name 'gabagool_rust'
```

**Solution**:
```bash
# Verify venv is active
which python

# Reinstall
cd rust/
maturin develop --release
pip list | grep gabagool  # Should show module
```

---

### Issue 3: PyO3 Async Not Working

**Symptom**:
```
RuntimeError: coroutine object has no attribute __await__
```

**Solution**:
```rust
// Make sure you're using pyo3-asyncio correctly:
use pyo3_asyncio;

#[pymethods]
impl MyStruct {
    fn async_method<'py>(&self, py: Python<'py>) -> PyResult<&'py PyAny> {
        pyo3_asyncio::tokio::future_into_py(py, async move {
            // Your async code
            Ok(())
        })
    }
}
```

---

### Issue 4: Performance Not As Expected

**Symptom**: Only 3-5x speedup instead of 10-20x

**Debug Steps**:
```bash
# Profile Rust code
cd rust/
cargo build --release
cargo flamegraph --bench my_bench

# Check if using release build
python -c "import gabagool_rust; print(gabagool_rust.__file__)"
# Should show path with "release" in it

# Verify parallelism (order execution)
# Should see tokio::join! in execution.rs

# Check for unnecessary allocations
# Look for lots of .clone() or .to_string()
```

---

## Appendix: Additional Rust Resources

### A. Other Rust CLOB Clients (For Reference Only)

**Not recommended for use, but interesting to study:**

#### A.1: TechieBoy/polymarket-rs-client
- **URL**: https://github.com/TechieBoy/polymarket-rs-client
- **Status**: Alpha, active development
- **Claims**: 1.5-4x faster than Python, 10x less memory
- **Why Not Use**: Community-maintained, official client is better
- **Learn From**: Memory usage patterns, client architecture

#### A.2: floor-licker/polyfill-rs
- **URL**: https://github.com/floor-licker/polyfill-rs
- **Status**: Performance-focused fork
- **Claims**: 21% faster than other Rust clients (SIMD JSON)
- **Why Not Use**: Premature optimization, marginal gains
- **Learn From**: SIMD techniques, HTTP/2 tuning (if you need that extra 2ms)

#### A.3: CarlWiles/polymarket-sdk
- **URL**: https://github.com/CarlWiles/polymarket-sdk
- **Status**: Community SDK
- **Why Not Use**: Official client exists and is better maintained
- **Learn From**: Alternative API design patterns

---

### B. Trading Bot Implementations (Different Strategies)

**For architecture patterns only, NOT for direct use:**

#### B.1: terausss/polymarket-copy-trading-bot
- **URL**: https://github.com/terausss/polymarket-copy-trading-bot
- **Strategy**: Copy trading (mirrors whale wallets)
- **Status**: Public version available, private version requires payment
- **Learn From**: Wallet monitoring patterns, trade detection
- **Why Not Use**: Different strategy, public version may be outdated

#### B.2: vladmeer/polymarket-copy-trading-bot
- **URL**: https://github.com/vladmeer/polymarket-copy-trading-bot
- **Strategy**: Copy trading with multiple language implementations
- **Note**: Mentions Rust version but focuses on TypeScript/Python
- **Learn From**: Multi-language architecture approach

#### B.3: Novus-Tech-LLC/Polymarket-Copytrading-Bot
- **URL**: https://github.com/Novus-Tech-LLC/Polymarket-Copytrading-Bot
- **Strategy**: Enterprise copy trading
- **Status**: Mentions Rust version for "high performance"
- **Learn From**: Enterprise-grade architecture, risk management patterns

#### B.4: dappboris-dev/polymarket-trading-bot
- **URL**: https://github.com/dappboris-dev/polymarket-trading-bot
- **Strategy**: Arbitrage but different approach (oracle price vs market)
- **Language**: TypeScript primarily
- **Learn From**: Different arbitrage detection logic

---

### C. HFT & Advanced Systems (Future Exploration)

**Only relevant if you scale to $100K+ capital:**

#### C.1: telepair/polymarket-hft
- **URL**: https://lib.rs/crates/polymarket-hft
- **Status**: Pre-alpha (v0.0.x)
- **Architecture**: Event-driven HFT system with policy engine
- **Why Not Use Yet**: Not production-ready
- **Future Use**: If you need multi-strategy HFT system at scale

**Features** (planned for v1.0+):
- Redis state management
- TimescaleDB archiving
- Policy engine (YAML/JSON rules)
- Multi-strategy dispatcher

**When to Revisit**: 
- When capital > $100K
- Running 10+ strategies simultaneously
- Need centralized state management

---

### D. Infrastructure Tools

#### D.1: Official Polymarket Python Client
- **URL**: https://github.com/Polymarket/py-clob-client
- **Why Relevant**: Your Python base uses this
- **Use For**: Understanding official API patterns
- **Note**: Keep for Python orchestration layer

#### D.2: Polymarket Agents Framework
- **URL**: https://github.com/Polymarket/agents
- **Language**: Python
- **Purpose**: AI-driven trading (LLM integration)
- **Why Interesting**: Shows official Anthropic/Polymarket integration patterns
- **Not Relevant**: For AI prediction, not arbitrage

---

## Quick Reference: Key Commands

```bash
# Setup
rustup component add rust-analyzer
pip install maturin

# Build & Install
cd rust/
maturin develop                 # Debug build
maturin develop --release       # Release build

# Testing
cargo test                      # Rust tests
pytest tests/                   # Python integration tests

# Benchmarking
cargo bench                     # Rust benchmarks
python benchmark.py             # End-to-end benchmark

# Deploy
maturin build --release
scp target/wheels/*.whl vps:/tmp/
ssh vps "pip install /tmp/gabagool_rust-*.whl"

# Monitor
python monitor_performance.py
```

---

## Summary

### What You're Building

**Foundation**: Official Polymarket Rust client (rs-clob-client v0.3)

**Custom Components** (convert from Python):
1. WebSocket handler (5-10ms target)
2. Arbitrage detection (<1ms target)
3. Order execution (20-30ms target)
4. Position tracking (<1ms target)

**Total Target**: <40ms hot path (vs 380ms Python = 9.5x faster)

### What You're NOT Building

❌ CLOB client from scratch (use official)
❌ Cross-platform arbitrage (different strategy)
❌ Copy trading bot (different strategy)
❌ HFT infrastructure (overkill for gabagool)

### Timeline

- **Week 1-2**: Python bot (proven strategy)
- **Week 3**: Rust conversion (this guide) - 18-20 hours
- **Week 4**: Production validation

### Next Steps

1. ✅ Ensure Python bot is working (Phase 0)
2. ✅ Clone official rs-clob-client (Phase 1)
3. ✅ Follow Phase 2 with Claude Code (component conversion)
4. ✅ Build, test, deploy (Phases 3-4)
5. ✅ Validate performance (Phase 5)

**This guide is your roadmap. Follow it sequentially with Claude Code assistance for best results.**

---

*End of Operational Rust Integration Guide*
