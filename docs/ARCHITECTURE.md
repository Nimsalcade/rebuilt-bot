# Gabagool Bot - Architecture

## Overview

Gabagool uses a **hybrid architecture** combining the best components from 4 proven Polymarket bot repositories.

## Component Sources

| Component | Source Repository | Purpose |
|-----------|-------------------|---------|
| Base Infrastructure | discountry/polymarket-trading-bot | API client, wallet, orders |
| Position Tracker | Trust412/Polymarket-spike-bot-v1 | Thread-safe position management |
| Risk Manager | lorine93s/polymarket-market-maker-bot | Pre-trade validation |
| Auto Redeemer | lorine93s/polymarket-market-maker-bot | Settlement automation |
| Stats Tracker | warproxxx/poly-maker | Performance metrics |
| Position Merger | warproxxx/poly-maker | Gas optimization |

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       GABAGOOL BOT                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │   Gamma     │    │  WebSocket  │    │    CLOB     │         │
│  │   Client    │    │   Client    │    │   Client    │         │
│  │  (markets)  │    │  (prices)   │    │  (orders)   │         │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘         │
│         │                  │                  │                 │
│         └──────────────────┴──────────────────┘                 │
│                            │                                    │
│                     ┌──────▼──────┐                             │
│                     │ Trading Bot │                             │
│                     └──────┬──────┘                             │
│                            │                                    │
│  ┌─────────────────────────┼─────────────────────────┐         │
│  │                         │                         │         │
│  │    ┌───────────────────┐│┌───────────────────┐   │         │
│  │    │   Risk Manager    │││  Position Tracker │   │         │
│  │    │  (pre-validate)   │││  (track pairs)    │   │         │
│  │    └─────────┬─────────┘│└─────────┬─────────┘   │         │
│  │              │          │          │             │         │
│  │              └──────────┴──────────┘             │         │
│  │                         │                        │         │
│  │              ┌──────────▼──────────┐             │         │
│  │              │ Gabagool Strategy   │             │         │
│  │              │ (arbitrage logic)   │             │         │
│  │              └──────────┬──────────┘             │         │
│  │                         │                        │         │
│  └─────────────────────────┼────────────────────────┘         │
│                            │                                    │
│  ┌─────────────────────────┼─────────────────────────┐         │
│  │                         │                         │         │
│  │  ┌──────────────┐  ┌────▼─────┐  ┌─────────────┐ │         │
│  │  │ Auto Redeem  │  │ Database │  │Stats Tracker│ │         │
│  │  │ (settlement) │  │ (SQLite) │  │ (metrics)   │ │         │
│  │  └──────────────┘  └──────────┘  └─────────────┘ │         │
│  │                                                   │         │
│  └───────────────────────────────────────────────────┘         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Market Discovery
```
Gamma API → GammaClient → find_15min_markets() → Market objects
```

### 2. Price Monitoring
```
WebSocket → price updates → Strategy.scan_opportunities()
```

### 3. Arbitrage Execution
```
Opportunity detected
    → RiskManager.validate_arbitrage()
    → Bot.place_order(YES)
    → Bot.place_order(NO)
    → PositionTracker.add_position()
    → StatsTracker.record_trade()
    → Database.save_trade()
```

### 4. Settlement
```
AutoRedeemer.run_continuous()
    → check_market_resolved()
    → redeem_position()
    → PositionTracker.mark_resolved()
    → StatsTracker.update_trade_result()
    → Database.save_settlement()
```

## Thread Safety

The PositionTracker uses threading locks for concurrent access:

```python
class PositionTracker:
    def __init__(self):
        self.lock = threading.Lock()

    def add_position(self, ...):
        with self.lock:
            # Thread-safe updates
```

## Configuration

Configuration follows a hierarchy:

1. Default values (code)
2. `config/default.yaml`
3. `config/production.yaml` (overrides)
4. Environment variables (highest priority)

## Database Schema

### positions table
- market_id (PK)
- yes_shares, yes_avg_cost, yes_total_cost
- no_shares, no_avg_cost, no_total_cost
- opened_at, resolved, profit

### trades table
- id, market_id, side, shares, price, cost, timestamp

### settlements table
- id, market_id, winning_side, profit, settled_at
