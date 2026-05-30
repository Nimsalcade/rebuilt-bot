# Gabagool Bot - Component Integration Plan

*Created: 2026-01-26*
*Purpose: Detailed plan for stitching together components from 4 sample repositories*

---

## Executive Summary

This document outlines the specific integration tasks required to transform the gabagool scaffolds into working code by extracting and adapting components from 4 sample repositories.

**Source Repositories:**
| Repository | Location | Primary Components |
|------------|----------|-------------------|
| discountry-base | `samples/discountry-base/` | API clients, order execution, wallet management |
| trust412-spike | `samples/trust412-spike/` | Thread-safe state, position tracking patterns |
| warproxxx-maker | `samples/warproxxx-maker/` | Stats tracking, order book analysis |
| lorine93s-mm | `samples/lorine93s-mm/` | Risk manager, auto-redemption |

---

## Integration Tasks by Module

### 1. Core API Client (`src/client.py`)

**Source:** `samples/discountry-base/src/client.py` (769 lines)

**Status:** Scaffold → Need full implementation

**Extract:**
- [ ] `ApiClient` base class with retry logic
- [ ] `ClobClient` - CLOB API interactions (orderbook, orders, trades)
- [ ] `RelayerClient` - Gasless transaction support
- [ ] `ApiCredentials` dataclass
- [ ] HMAC authentication headers (`_build_headers`)

**Key Methods to Extract:**
```python
# From discountry-base/src/client.py
- _request()           # HTTP with retries (lines 111-171)
- _build_headers()     # HMAC auth (lines 223-292)
- get_order_book()     # Orderbook query (lines 383-397)
- get_open_orders()    # User orders (lines 415-435)
- post_order()         # Submit order (lines 484-520)
- cancel_order()       # Cancel order (lines 522-542)
- derive_api_key()     # L2 auth (lines 294-327)
```

**Adaptations Needed:**
- Remove gasless/relayer code if not using Builder Program
- Simplify to essential CLOB operations
- Add async wrappers using `asyncio.to_thread()`

---

### 2. Trading Bot (`src/bot.py`)

**Source:** `samples/discountry-base/src/bot.py` (623 lines)

**Status:** Scaffold → Need full implementation

**Extract:**
- [ ] `TradingBot` class initialization
- [ ] `OrderSigner` integration for order signing
- [ ] `place_order()` / `place_orders()` methods
- [ ] `cancel_order()` / `cancel_all_orders()` methods
- [ ] `get_open_orders()` / `get_trades()` methods
- [ ] `OrderResult` dataclass

**Key Methods to Extract:**
```python
# From discountry-base/src/bot.py
- __init__()           # Client initialization (lines 118-207)
- place_order()        # Order placement (lines 288-346)
- cancel_order()       # Order cancellation (lines 383-408)
- get_open_orders()    # Query orders (lines 460-473)
- get_order_book()     # Market data (lines 514-528)
```

**Additional Files Needed:**
- `samples/discountry-base/src/signer.py` → New `src/signer.py`
- `samples/discountry-base/src/config.py` → Adapt to `src/config.py`
- `samples/discountry-base/src/crypto.py` → New `src/crypto.py` (key encryption)

---

### 3. Position Tracker (`src/position_tracker.py`)

**Source:** `samples/trust412-spike/main.py` (lines 253-464)

**Status:** ✅ Already implemented - verify alignment

**Current Implementation vs Source:**

| Feature | Our Implementation | Trust412 Source |
|---------|-------------------|-----------------|
| Thread safety | `threading.Lock()` ✅ | `threading.Lock()` ✅ |
| Dataclasses | `ArbitragePosition` ✅ | `TradeInfo`, `PositionInfo` |
| Time limits | `holding_time_limit` ✅ | `HOLDING_TIME_LIMIT` ✅ |
| Max concurrent | `max_concurrent` ✅ | `MAX_CONCURRENT_TRADES` ✅ |
| Cleanup | `cleanup_expired()` ✅ | Manual in thread |

**Verify/Align:**
- [ ] Ensure `ArbitragePosition` has all needed fields
- [ ] Add paired YES/NO position concept (not in trust412)
- [ ] Confirm weighted average cost calculation

**Gap: Our tracker is arbitrage-specific (tracks YES+NO pairs), while trust412 is general position tracking. Our design is correct for gabagool.**

---

### 4. Risk Manager (`src/risk_manager.py`)

**Source:** `samples/lorine93s-mm/src/risk/risk_manager.py` (84 lines)

**Status:** ✅ Already implemented - verify alignment

**Current Implementation vs Source:**

| Feature | Our Implementation | Lorine93s Source |
|---------|-------------------|------------------|
| Exposure check | `_check_total_exposure()` | `check_exposure_limits()` |
| Position size | `_check_position_limit()` | `check_position_size()` |
| Validation | `validate_arbitrage()` | `validate_order()` |
| Stop trading | `check_circuit_breakers()` | `should_stop_trading()` |

**Key Difference:** Our implementation includes arbitrage-specific checks (combined cost, profit margin) that lorine93s doesn't have.

**Verify/Align:**
- [ ] Add inventory skew check (from lorine93s)
- [ ] Ensure settings names align with config
- [ ] Add `InventoryManager` dependency (if needed)

---

### 5. Stats Tracker (`src/stats_tracker.py`)

**Source:** `samples/warproxxx-maker/poly_stats/account_stats.py` (136 lines)

**Status:** ✅ Already implemented - different approach

**Current vs Source:**

| Feature | Our Implementation | Warproxxx Source |
|---------|-------------------|------------------|
| Storage | JSON file | Google Sheets |
| Metrics | Win rate, avg profit, etc. | Positions, earnings |
| Update | `record_trade()` | `update_stats_once()` |

**Gap:** Warproxxx uses Google Sheets for live dashboard - we use JSON for simplicity. Our approach is correct.

**Optional Enhancement:**
- [ ] Add earnings tracking pattern from warproxxx
- [ ] Add position summary calculations

---

### 6. Auto Redeemer (`src/auto_redeem.py`)

**Source:** `samples/lorine93s-mm/src/services/auto_redeem.py` (61 lines)

**Status:** Scaffold → Need implementation

**Extract:**
- [ ] `AutoRedeem` class with httpx client
- [ ] `check_redeemable_positions()` - query API
- [ ] `redeem_position()` - execute redemption
- [ ] `auto_redeem_all()` - polling loop

**Adaptations Needed:**
- Replace `httpx` with `aiohttp` for consistency
- Add `Settings` integration with our config
- Add threshold configuration (`redeem_threshold_usd`)

---

### 7. Poly Merger / Gas Optimization (`src/poly_merger.py`)

**Source:** Need to find in warproxxx-maker

**Status:** Scaffold → Need implementation

**Note:** The poly_merger functionality may be in:
- `samples/warproxxx-maker/poly_data/trading_utils.py`
- Or a separate file we haven't found yet

**Tasks:**
- [ ] Locate merger code in warproxxx-maker
- [ ] Extract position merging logic
- [ ] Adapt for gabagool's YES/NO pair structure

---

### 8. Gamma Client (`src/gamma_client.py`)

**Source:** `samples/discountry-base/src/gamma_client.py`

**Status:** Scaffold → Need implementation

**Extract:**
- [ ] Market discovery queries
- [ ] 15-minute BTC/ETH/SOL market filtering
- [ ] Market resolution status checks

---

### 9. WebSocket Client (`src/websocket_client.py`)

**Source:** `samples/discountry-base/src/websocket_client.py`

**Status:** Scaffold → Need implementation

**Extract:**
- [ ] WebSocket connection management
- [ ] Real-time price subscriptions
- [ ] Orderbook update handling

---

### 10. Main Entry Point (`src/main.py`)

**Source:** Multiple references:
- `samples/discountry-base/scripts/run_bot.py`
- `samples/trust412-spike/main.py` (thread management)

**Status:** Scaffold → Need implementation

**Patterns to Extract:**
- [ ] Thread management from trust412 (`ThreadManager` class)
- [ ] Signal handlers for graceful shutdown
- [ ] Configuration loading
- [ ] Component wiring

---

## Integration Order (Recommended)

### Phase 1: Core Infrastructure (Day 1)
1. **config.py** - Load configuration, environment variables
2. **signer.py** - Order signing (copy from discountry)
3. **crypto.py** - Key encryption (copy from discountry)
4. **client.py** - API client (extract from discountry)

### Phase 2: Trading Operations (Day 1-2)
5. **bot.py** - Trading bot (extract from discountry)
6. **gamma_client.py** - Market discovery
7. **websocket_client.py** - Real-time prices

### Phase 3: Integration & Testing (Day 2)
8. **main.py** - Wire components together
9. **risk_manager.py** - Verify integration
10. **position_tracker.py** - Verify integration
11. **stats_tracker.py** - Verify integration

### Phase 4: Advanced Features (Day 3)
12. **auto_redeem.py** - Auto settlement
13. **poly_merger.py** - Gas optimization (if found)

---

## Variable Alignment Checklist

### Configuration Variables
| Our Name | Discountry | Trust412 | Lorine93s |
|----------|------------|----------|-----------|
| `private_key` | `PK` / `private_key` | `PK` | N/A |
| `safe_address` | `safe_address` | `YOUR_PROXY_WALLET` | N/A |
| `clob_api_url` | `host` | N/A | `polymarket_api_url` |
| `chain_id` | `chain_id` | `137` | N/A |

### Position Tracking Variables
| Our Name | Trust412 Equivalent |
|----------|---------------------|
| `market_id` | `asset_id` |
| `yes_shares` | `amount` |
| `yes_avg_cost` | `entry_price` |
| `holding_time_limit` | `HOLDING_TIME_LIMIT` |
| `max_concurrent` | `MAX_CONCURRENT_TRADES` |

### Risk Manager Variables
| Our Name | Lorine93s Equivalent |
|----------|----------------------|
| `max_position_per_market` | `max_position_size_usd` |
| `max_total_exposure` | `max_exposure_usd` |
| `min_profit_margin` | N/A (gabagool-specific) |
| `max_combined_cost` | N/A (gabagool-specific) |

---

## Files to Create (New)

| File | Source | Purpose |
|------|--------|---------|
| `src/signer.py` | discountry | Order signing |
| `src/crypto.py` | discountry | Key encryption |
| `src/config.py` | discountry/custom | Configuration management |
| `src/http.py` | discountry | Thread-local HTTP sessions |

---

## Files to Update (Existing)

| File | Changes Needed |
|------|----------------|
| `src/client.py` | Replace scaffold with discountry implementation |
| `src/bot.py` | Replace scaffold with discountry implementation |
| `src/gamma_client.py` | Replace scaffold with discountry implementation |
| `src/websocket_client.py` | Replace scaffold with discountry implementation |
| `src/auto_redeem.py` | Replace scaffold with lorine93s implementation |
| `src/main.py` | Wire all components together |

---

## Testing Strategy

### Unit Tests (No API)
1. Run existing tests for position_tracker, risk_manager, stats_tracker
2. Add tests for new config, signer modules

### Live Connection Tests
1. API connection test (verify credentials)
2. Wallet balance test
3. Order placement test (dry run mode)

### Integration Tests
1. Full loop: discover market → check prices → validate → (dry run) order
2. Position tracking across multiple markets
3. Auto-redeem polling

---

## Next Action

**Immediate:** Start with Phase 1 - Core Infrastructure
1. Create `src/signer.py` from discountry
2. Create `src/crypto.py` from discountry
3. Create `src/http.py` from discountry
4. Update `src/client.py` with discountry implementation

---

*This plan will be updated as integration progresses.*
