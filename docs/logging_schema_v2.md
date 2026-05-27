# Gabagool Comprehensive Logging Schema v2

## Purpose

Capture all data necessary to:
1. Assess arbitrage spread opportunities
2. Model leg risk (price movement between executions)
3. Analyze liquidity depth and fill probability
4. Backtest execution strategies

---

## Log File Structure

### Primary Log: `market_snapshots.jsonl`

JSON Lines format (one JSON object per line) for easy parsing and streaming.

```json
{
  "ts": "2026-01-27T04:56:36.123Z",
  "ts_epoch_ms": 1769486196123,
  "scan_id": 1267,
  "window": {
    "start": "2026-01-27T04:45:00Z",
    "end": "2026-01-27T05:00:00Z",
    "seconds_in": 696,
    "seconds_remain": 204,
    "phase": "middle"
  },
  "markets": [
    {
      "coin": "BTC",
      "slug": "btc-updown-15m-1769485500",
      "up": {
        "token_id": "abc123...",
        "best_bid": 0.19,
        "best_ask": 0.20,
        "spread": 0.01,
        "book": {
          "bids": [
            {"price": 0.19, "size": 150.0},
            {"price": 0.18, "size": 300.0},
            {"price": 0.17, "size": 500.0}
          ],
          "asks": [
            {"price": 0.20, "size": 120.0},
            {"price": 0.21, "size": 250.0},
            {"price": 0.22, "size": 400.0}
          ]
        },
        "depth_100": 0.205,
        "depth_200": 0.210,
        "depth_500": 0.218
      },
      "down": {
        "token_id": "def456...",
        "best_bid": 0.80,
        "best_ask": 0.81,
        "spread": 0.01,
        "book": {
          "bids": [
            {"price": 0.80, "size": 200.0},
            {"price": 0.79, "size": 350.0},
            {"price": 0.78, "size": 600.0}
          ],
          "asks": [
            {"price": 0.81, "size": 180.0},
            {"price": 0.82, "size": 280.0},
            {"price": 0.83, "size": 450.0}
          ]
        },
        "depth_100": 0.812,
        "depth_200": 0.815,
        "depth_500": 0.822
      },
      "combined": {
        "best_ask": 1.01,
        "best_bid": 0.99,
        "midpoint": 1.00,
        "margin_ask": -0.01,
        "margin_bid": 0.01
      },
      "execution_sim": {
        "size_100": {"cost": 101.50, "avg_price": 1.015, "profit": -1.50},
        "size_200": {"cost": 203.20, "avg_price": 1.016, "profit": -3.20},
        "size_500": {"cost": 509.00, "avg_price": 1.018, "profit": -9.00}
      }
    }
  ],
  "best_opportunity": {
    "coin": "ETH",
    "combined_ask": 0.99,
    "margin": 0.01,
    "max_profitable_size": 150.0
  },
  "alerts": []
}
```

---

## Field Definitions

### Timestamp Fields
| Field | Type | Description |
|-------|------|-------------|
| `ts` | ISO8601 | Human-readable UTC timestamp with milliseconds |
| `ts_epoch_ms` | int | Unix epoch milliseconds (for precise calculations) |
| `scan_id` | int | Sequential scan counter |

### Window Fields
| Field | Type | Description |
|-------|------|-------------|
| `window.start` | ISO8601 | 15-minute window start time |
| `window.end` | ISO8601 | 15-minute window end time |
| `window.seconds_in` | int | Seconds elapsed in current window |
| `window.seconds_remain` | int | Seconds until window closes |
| `window.phase` | string | "start", "middle", or "end" |

### Order Book Fields
| Field | Type | Description |
|-------|------|-------------|
| `best_bid` | float | Highest bid price |
| `best_ask` | float | Lowest ask price |
| `spread` | float | best_ask - best_bid |
| `book.bids[]` | array | Top N bid levels (price, size) |
| `book.asks[]` | array | Top N ask levels (price, size) |
| `depth_N` | float | Volume-weighted avg price to fill $N |

### Combined/Arbitrage Fields
| Field | Type | Description |
|-------|------|-------------|
| `combined.best_ask` | float | UP_ask + DOWN_ask (cost to buy both) |
| `combined.best_bid` | float | UP_bid + DOWN_bid (revenue to sell both) |
| `combined.margin_ask` | float | 1.0 - combined_ask (profit if < 0 is loss) |
| `combined.margin_bid` | float | combined_bid - 1.0 |

### Execution Simulation Fields
| Field | Type | Description |
|-------|------|-------------|
| `execution_sim.size_N.cost` | float | Total cost to buy $N of UP + DOWN |
| `execution_sim.size_N.avg_price` | float | Effective combined price |
| `execution_sim.size_N.profit` | float | Expected profit (negative = loss) |

---

## Secondary Logs

### `opportunities.jsonl` - Triggered when combined_ask < 1.00

```json
{
  "ts": "2026-01-27T04:56:36.123Z",
  "ts_epoch_ms": 1769486196123,
  "coin": "BTC",
  "combined_ask": 0.990,
  "margin": 0.010,
  "up_ask": 0.480,
  "down_ask": 0.510,
  "up_size_at_best": 150.0,
  "down_size_at_best": 200.0,
  "min_size": 150.0,
  "max_profitable_size": 320.0,
  "book_snapshot": { ... },
  "prev_scan_combined": 1.010,
  "next_scan_combined": null
}
```

### `leg_risk_analysis.jsonl` - Tracks price changes between scans

```json
{
  "ts": "2026-01-27T04:56:36.123Z",
  "coin": "BTC",
  "interval_ms": 10234,
  "up_change": 0.030,
  "down_change": -0.025,
  "combined_change": 0.005,
  "up_book_change": {
    "best_ask_moved": true,
    "size_at_best_change": -50.0
  },
  "volatility_1m": 0.045,
  "volatility_5m": 0.082
}
```

---

## Configuration

```yaml
# logging_config.yaml
logging:
  base_dir: "data/logs"

  snapshots:
    file: "market_snapshots.jsonl"
    interval_normal: 10  # seconds
    interval_aggressive: 1  # seconds
    book_depth: 5  # number of price levels to capture

  opportunities:
    file: "opportunities.jsonl"
    threshold: 1.00  # log when combined_ask < this

  leg_risk:
    file: "leg_risk_analysis.jsonl"
    enabled: true

  rotation:
    max_size_mb: 100
    max_files: 10
    compress: true

  execution_sim_sizes: [50, 100, 200, 500, 1000]
```

---

## Data Volume Estimates

| Scenario | Snapshots/hour | Size/hour | Size/day |
|----------|----------------|-----------|----------|
| Normal (10s) | 360 | ~2 MB | ~48 MB |
| Aggressive (1s) | 3600 | ~20 MB | ~480 MB |
| Mixed (realistic) | ~600 | ~4 MB | ~96 MB |

With compression: ~20-30 MB/day

---

## Analysis Queries

### 1. Find all opportunities
```bash
jq 'select(.best_opportunity.margin > 0)' market_snapshots.jsonl
```

### 2. Calculate leg risk statistics
```bash
jq -s '[.[].markets[].up.best_ask] |
  {min: min, max: max, avg: (add/length)}' market_snapshots.jsonl
```

### 3. Backtest execution at $200
```bash
jq '.markets[] |
  select(.execution_sim.size_200.profit > 0) |
  {coin, profit: .execution_sim.size_200.profit}' market_snapshots.jsonl
```

---

## Implementation Notes

1. **Atomic writes**: Use temp file + rename to prevent corruption
2. **Buffering**: Buffer up to 10 entries before flush (configurable)
3. **Error handling**: Log errors to separate `errors.log`
4. **Timestamps**: Use `time.time_ns()` for sub-millisecond precision
5. **Order book depth**: Capture top 5 levels minimum, top 10 preferred
