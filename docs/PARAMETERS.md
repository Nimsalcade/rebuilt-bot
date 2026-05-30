# Gabagool Bot - Parameters Reference

## Strategy Parameters

### Entry Thresholds

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `yes_threshold` | 0.48 | 0.40-0.52 | Buy YES if price below this |
| `no_threshold` | 0.48 | 0.40-0.52 | Buy NO if price below this |
| `max_combined_cost` | 0.97 | 0.95-0.98 | Max total cost for YES + NO |
| `min_profit_margin` | 0.02 | 0.02-0.05 | Minimum profit margin to enter |

**Example:**
- YES @ $0.45 + NO @ $0.48 = $0.93 combined
- Profit margin: 1.00 - 0.93 = 0.07 (7%)
- Passes all thresholds

### Trade Sizing

| Parameter | Default | Description |
|-----------|---------|-------------|
| `trade_size` | 5.0 | USD per side (total = 2x) |

## Risk Parameters

### Position Limits

| Parameter | $100 Capital | $1k Capital | $15k Capital |
|-----------|--------------|-------------|--------------|
| `max_position_per_market` | $50 | $200 | $1,000 |
| `max_total_exposure` | $100 | $700 | $10,000 |
| `max_concurrent_arbitrages` | 2 | 3 | 5 |
| `max_unpaired_exposure` | $10 | $100 | $1,500 |

### Time Limits

| Parameter | Value | Description |
|-----------|-------|-------------|
| `holding_time_limit` | 1800s (30 min) | Max time to complete pair |
| `min_time_to_resolution` | 120s (2 min) | Don't enter near expiry |
| `redeem_check_interval` | 300s (5 min) | Settlement polling |

### Circuit Breakers

| Trigger | Action |
|---------|--------|
| 3 consecutive failures | Pause trading, alert |
| Wallet balance < $10 | Stop new positions |
| Daily drawdown > 15% | Stop trading for day |

## Market Selection

| Parameter | Default | Description |
|-----------|---------|-------------|
| `assets` | BTC, ETH, SOL | Target assets |
| `duration_minutes` | 15 | Target market duration |

## Tuning Guide

### Conservative (New Users)

```yaml
strategy:
  yes_threshold: 0.45
  no_threshold: 0.45
  max_combined_cost: 0.95
  min_profit_margin: 0.04
  trade_size: 3.0

risk:
  max_concurrent_arbitrages: 1
  max_total_exposure: 50.0
```

### Balanced (Default)

```yaml
strategy:
  yes_threshold: 0.48
  no_threshold: 0.48
  max_combined_cost: 0.97
  min_profit_margin: 0.02
  trade_size: 5.0

risk:
  max_concurrent_arbitrages: 3
  max_total_exposure: 500.0
```

### Aggressive (Experienced)

```yaml
strategy:
  yes_threshold: 0.50
  no_threshold: 0.50
  max_combined_cost: 0.98
  min_profit_margin: 0.01
  trade_size: 20.0

risk:
  max_concurrent_arbitrages: 5
  max_total_exposure: 5000.0
```

## Environment Variables

All config parameters can be overridden via environment:

```bash
# Strategy
export YES_THRESHOLD=0.48
export NO_THRESHOLD=0.48
export MAX_COMBINED_COST=0.97
export MIN_PROFIT_MARGIN=0.02
export TRADE_SIZE=5.0

# Risk
export MAX_POSITION_PER_MARKET=100.0
export MAX_TOTAL_EXPOSURE=500.0
export MAX_CONCURRENT_ARBITRAGES=3
```

## Parameter Interactions

### Threshold + Trade Size
Higher thresholds = more opportunities but lower margins.
Larger trade sizes require more liquidity.

### Exposure + Concurrent Positions
`max_total_exposure` / `max_concurrent_arbitrages` = approx exposure per position.

### Time Limits + Market Duration
`holding_time_limit` should be < market duration to ensure settlement.
