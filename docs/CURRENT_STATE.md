# Gabagool - Current State (2026-01-27)

## Active Processes

```bash
# Three tmux sessions running:
tmux session: monitor_15m_v5           # Conservative paper trading
tmux session: monitor_15m_v5_aggressive # Aggressive paper trading
tmux session: rust_build               # Rust hot path compilation
```

## v5 Strategy: Liquidity-First Stacking

**Key Innovation**: Zero slippage by design - only trades at BEST ask price level.

v4 Problem: $350 trades caused slippage through order book, turning 1% theoretical profit into 2-3% actual loss.

v5 Solution: Trade only what's available at best price (often $5-30), stack multiple small trades.

**Live Performance** (as of session end):
| Instance | Trades | Net PnL | Equity |
|----------|--------|---------|--------|
| v5 | 4 | +$0.80 | $2,000.80 |
| aggressive | 4 | +$0.34 | $2,000.34 |

## Running Instances

| Instance | Min Margin | Max Trade | tmux Session |
|----------|------------|-----------|--------------|
| `v5` | 0.50% | $100 | `monitor_15m_v5` |
| `aggressive` | 0.20% | $500 | `monitor_15m_v5_aggressive` |

## Rust Hot Path (In Progress)

**Status**: Phase 1 & 2 complete, build in progress

**Goal**: 10x speedup on critical path (<40ms vs 380ms Python)

```
rust/src/
├── lib.rs          # PyO3 module bindings
├── position.rs     # Position tracking (<1ms)
├── strategy.rs     # Arbitrage detection (<1ms)
├── ws_handler.rs   # Price feed cache (5-10ms)
└── execution.rs    # Order execution (20-30ms)
```

**Build Command**:
```bash
# Check build progress
tail -f rust/build.log
tmux attach -t rust_build

# After build completes
cd rust && maturin develop
python -c "import gabagool_rust; print(gabagool_rust.health_check())"
```

## Key Parameters (v5)

| Setting | v5 (Conservative) | Aggressive |
|---------|-------------------|------------|
| Initial equity | $2,000 | $2,000 |
| Min margin | 0.50% | 0.20% |
| Max trade size | $100 | $500 |
| Max stack/window | 10 | 10 |
| Gas cost | $0.003/tx | $0.003/tx |
| Scan interval | 500ms | 500ms |

## Data Files

**Per-instance** (in `data/logs/`):
- `{instance}_paper_trades.jsonl` - Trade history
- `{instance}_paper_pnl_summary.json` - Equity & stats

**Shared**:
- `opportunities_*.jsonl` - Detected opportunities
- `market_snapshots_*.jsonl` - Raw data (8h retention)

## Quick Commands

```bash
# List sessions
tmux list-sessions

# Attach to session
tmux attach -t monitor_15m_v5
tmux attach -t monitor_15m_v5_aggressive
tmux attach -t rust_build

# Check P&L
cat data/logs/v5_paper_pnl_summary.json | jq
cat data/logs/aggressive_paper_pnl_summary.json | jq

# Start new instance
source .venv/bin/activate
python scripts/monitor_15m_v5.py --instance mytest --fresh --min-margin 0.003

# Kill instance
tmux kill-session -t monitor_15m_v5
```

## Recent Changes (2026-01-27)

### Earlier (v5 Monitor)
1. Created monitor_v5 with liquidity-first stacking strategy
2. Fixed order book parsing bug (API returns asks descending)
3. Added multi-instance support (--instance, --fresh flags)
4. Updated gas cost to realistic Polygon estimate ($0.003/tx)
5. Added Discord webhook for weekly reports
6. Shut down v4, running two v5 instances

### Later (Rust Integration)
7. Added Rule 7 to CLAUDE.md (VPS disk space monitoring)
8. Installed Rust 1.93.0 + maturin
9. Cloned rs-clob-client and poly-kalshi-arb to rust-workspace/
10. Created 4 Rust hot path modules (position, strategy, ws_handler, execution)
11. Build running in background with vendored OpenSSL

## Polygon Gas Analysis

| Scenario | Per Order | 2 Orders (Arb) |
|----------|-----------|----------------|
| Normal | $0.002 | $0.004 |
| Busy | $0.004 | $0.008 |
| Config | $0.003 | $0.006 |

Break-even at 1% margin: ~$0.60 trade size
