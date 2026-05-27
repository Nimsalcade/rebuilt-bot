# PolyBotTOP

**Automated Binary Prediction Market — Maker & Latency Arbitrage System**

*Version 1.0 | Built for Polymarket CLOB | Polygon Network*

---

## Overview

Lynx is a fully automated, high-frequency market-making and latency arbitrage system for Polymarket binary prediction markets (BTC/ETH up-or-down).

It runs two concurrent strategies across 4 active market windows simultaneously:

1. **Price Ladder (Farming)** — Posts 16 GTC limit orders per side at every $0.05 increment ($0.10–$0.85), blanketing the order book on both UP and DOWN. Delta-neutral by design — profits regardless of direction.
2. **Latency Sniping** — Subscribes to Binance Futures `aggTrade` WebSocket. Detects price spikes with a 5-second rolling momentum window and fires burst orders across all 4 markets in < 130ms.

```
Start capital: $986  →  Hard cap: $1,000  →  Withdraw profits every 2–3 days
```

---

## How It Works

```
Buy UP  @ $0.30 avg   ──┐
                         ├──▶  Combined cost $0.65  ──▶  Settle at $1.00  ──▶  +$0.35 profit
Buy DOWN @ $0.35 avg  ──┘         (< $1.00 ✅)             (one side wins)       per pair
```

The ladder posts on **both sides simultaneously**. When the market settles, one side pays $1.00. As long as the combined entry cost across both sides is below $1.00, profit is guaranteed — regardless of direction.

---

## Core Metrics

| Metric | Value |
|---|---|
| Burst execution latency | < 130ms |
| Simultaneous markets | 4 (hard cap) |
| Price levels per side per market | 16 |
| Shares per rung | 5 |
| Capital cap (global) | $1,000 |
| Capital cap (per window) | $250 |
| Hedge imbalance ratio | 3:1 max |
| Stop posting buffer | 60s before window close |

---

## Project Structure

```
lynx/
├── src/                  # Core source code
│   ├── main.py           # Entry point
│   ├── maker_loop.py     # Price ladder & window state machine
│   ├── window_manager.py # Session lifecycle, 4-market cap
│   ├── sniper.py         # Burst execution engine
│   ├── price_feed_manager.py  # Binance WebSocket feeds
│   ├── spike_detector.py # 5s momentum spike detection
│   ├── risk_manager.py   # Pre-trade validation
│   ├── trading_bot.py    # Polymarket CLOB client
│   ├── paper_trader.py   # Dry-run settlement simulation
│   └── database.py       # SQLite persistence
├── config/
│   ├── default.yaml      # Default parameters
│   └── production.yaml   # Live trading config
├── strategies/           # Strategy modules
├── tests/                # Test suite
├── scripts/              # Utility scripts
├── Makefile              # Run commands
└── requirements.txt      # Dependencies
```

---

## Quick Start

```bash
# 1. Clone & setup
git clone https://github.com/Nimsalcade/LYNX.git
cd LYNX
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure credentials
export POLY_PRIVATE_KEY="your_eoa_private_key"
export POLY_SAFE_ADDRESS="your_polymarket_wallet_address"

# 3. Dry run (paper trading — no real funds)
make run

# 4. Live trading
make run-live
```

---

## Configuration

Key parameters in `config/production.yaml`:

| Parameter | Default | Description |
|---|---|---|
| `max_total_exposure` | 1000.0 | Hard global capital cap (USD) |
| `max_position_per_market` | 250.0 | Per-window capital cap (USD) |
| `max_concurrent_arbitrages` | 4 | Max simultaneous markets |
| `spike_threshold` | 0.020 | % momentum to trigger snipe |
| `ladder_shares_per_step` | 5 | Shares per price rung |
| `stop_posting_buffer_s` | 60 | Seconds before close to stop posting |
| `hedge_imbalance_ratio` | 3.0 | Max UP:DOWN share ratio |

---

## State Machine

Each active market window runs an independent state machine:

```
FARMING ──[spike detected]──▶ SNIPING ──▶ COOLDOWN ──▶ FARMING
   │                                                        │
   └──[window closing in 60s]──▶ HOLD ──[expired]──▶ DONE──┘
```

- **FARMING** — Full 16-rung ladder posted both sides, refreshed every 45s
- **SNIPING** — Burst engine fired on spike, aggressive GTC placed
- **COOLDOWN** — 45s pause, no new orders
- **HOLD** — Ladder cancelled, positions held to settlement
- **DONE** — Window expired, settlement queued

---

## Risk Controls

- **No sell orders ever** — all positions held to settlement
- **$1,000 global cap** — counts both filled positions and locked resting orders
- **4-market hard cap** — WindowManager rejects new sessions beyond 4
- **Hedge ratio enforcement** — blocks one-sided exposure beyond 3:1
- **Circuit breakers** — consecutive failure pause, daily drawdown limit

---

## Deployment (VPS)

```bash
# On Ubuntu VPS
git clone https://github.com/Nimsalcade/LYNX.git /root/lynx
cd /root/lynx
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run in persistent tmux session
tmux new -s lynx
make run-live
```

---

## Security

- **Never commit private keys** — use environment variables only
- `.env` and `*.key` files are gitignored
- Safe address and signing key stored separately
- All live operations require `LIVE_MODE=true`

---

## License

MIT License
