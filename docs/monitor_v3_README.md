# Gabagool Monitor v3 - Order Book Logger

High-frequency order book capture for Polymarket 15-minute crypto prediction markets.

## Quick Commands

```bash
# Check if running
tmux list-sessions | grep gabagool

# View live output
tmux attach -t gabagool_v3
# (Ctrl+B then D to detach)

# Tail logs
tail -f ~/bots/gabagool/logs/monitor_v3.log

# Count today's records
wc -l ~/bots/gabagool/data/logs/market_snapshots_$(date +%Y%m%d).jsonl

# Check for opportunities
cat ~/bots/gabagool/data/logs/opportunities_$(date +%Y%m%d).jsonl

# Restart
cd ~/bots/gabagool
tmux kill-session -t gabagool_v3
tmux new-session -d -s gabagool_v3 \
  "source .venv/bin/activate && python scripts/monitor_15m_v3.py 2>&1 | tee logs/monitor_v3.log"
```

---

## What It Collects

| Data | Description |
|------|-------------|
| **Order book depth** | Top 10 bid/ask levels with prices and sizes |
| **Best bid/ask** | For both UP and DOWN tokens |
| **Combined metrics** | `combined_ask`, `combined_bid`, `margin` |
| **Execution simulations** | Cost/profit to fill $50, $100, $200, $500, $1000 |
| **Depth prices** | VWAP to fill various sizes |
| **Timestamps** | ISO8601 + epoch milliseconds |

**Assets**: BTC, ETH, SOL, XRP (MSTR excluded - no 15m markets exist)

---

## Output Files

```
data/logs/
├── market_snapshots_YYYYMMDD.jsonl   # Full order book data
└── opportunities_YYYYMMDD.jsonl       # When combined_ask < 1.00

logs/
└── monitor_v3.log                     # Human-readable console output
```

---

## Record Format (JSONL)

Each line is a complete JSON object:

```json
{
  "ts": "2026-01-27T06:15:32.123Z",
  "ts_epoch_ms": 1769494532123,
  "scan_id": 1542,
  "scan_duration_ms": 198.5,
  "window": {
    "seconds_in": 32,
    "seconds_remain": 868,
    "phase": "start"
  },
  "markets": [
    {
      "coin": "BTC",
      "slug": "btc-updown-15m-1769494500",
      "remaining_seconds": 868.0,
      "up": {
        "token_id": "530676081275...",
        "best_bid": 0.48,
        "best_ask": 0.50,
        "spread": 0.02,
        "book": {
          "bids": [{"price": 0.48, "size": 150.0}, ...],
          "asks": [{"price": 0.50, "size": 120.0}, ...]
        },
        "depth_prices": {"depth_100": 0.502, "depth_200": 0.508, ...}
      },
      "down": {
        "token_id": "425384620908...",
        "best_bid": 0.49,
        "best_ask": 0.51,
        "spread": 0.02,
        "book": {...}
      },
      "combined": {
        "best_ask": 1.01,
        "best_bid": 0.97,
        "margin": -0.01
      },
      "execution_sim": {
        "200": {"cost": 202.50, "avg_price": 1.0125, "profit": -2.50, "tokens": 200}
      }
    }
  ],
  "best_opportunity": {
    "coin": "BTC",
    "combined_ask": 1.01,
    "margin": -0.01
  }
}
```

---

## Scan Intervals

| Phase | Interval | When |
|-------|----------|------|
| **Aggressive** | 500ms | First/last 120 seconds of each 15-min window |
| **Normal** | 2000ms | Middle of window |

**Why**: Price volatility is highest at window start (new market) and end (approaching resolution).

---

## Performance

| Metric | Value |
|--------|-------|
| API latency | ~180-200ms per call |
| Parallel fetch (8 tokens) | ~200-300ms |
| Scan overhead | ~50-100ms |
| **Effective rate** | ~2 scans/sec max |

---

## Storage

| Timeframe | Size (uncompressed) |
|-----------|---------------------|
| Per record | ~4 KB |
| Per hour (aggressive) | ~7 MB |
| Per day (mixed) | ~100-150 MB |

**Rotation**: Files rotate daily by date suffix.

---

## Analyzing Data

```python
import json

# Read all records
with open('data/logs/market_snapshots_20260127.jsonl') as f:
    for line in f:
        record = json.loads(line)
        for market in record['markets']:
            if market['combined']['best_ask'] < 0.99:
                print(f"Opportunity: {market['coin']} @ {market['combined']['best_ask']}")
```

```bash
# Find opportunities with jq
cat data/logs/market_snapshots_*.jsonl | \
  jq -c 'select(.best_opportunity.combined_ask < 0.99)'

# Extract combined values
cat data/logs/market_snapshots_*.jsonl | \
  jq -r '.markets[] | "\(.coin) \(.combined.best_ask)"'
```

---

## Configuration

Edit `scripts/monitor_15m_v3.py` or pass CLI args:

```bash
python scripts/monitor_15m_v3.py \
  --aggressive-interval 500 \
  --normal-interval 2000 \
  --depth 10 \
  --threshold 1.00 \
  --log-dir data/logs
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--aggressive-interval` | 500 | ms between scans during start/end phase |
| `--normal-interval` | 2000 | ms between scans during middle phase |
| `--depth` | 10 | Order book levels to capture |
| `--threshold` | 1.00 | Log to opportunities file when combined < this |
| `--log-dir` | data/logs | Output directory |

---

## Troubleshooting

**Monitor not running?**
```bash
tmux list-sessions  # Check if session exists
cat logs/monitor_v3.log | tail -50  # Check for errors
```

**No data files?**
```bash
ls -la data/logs/  # Check directory exists
```

**High combined values (1.5+)?**
Normal at window end - markets polarize as outcome becomes certain.

**API errors?**
Rate limiting is unlikely at configured intervals. Check network connectivity.
