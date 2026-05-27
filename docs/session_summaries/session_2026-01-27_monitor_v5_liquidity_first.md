# Session Summary: Monitor v5 - Liquidity-First Strategy

**Date**: 2026-01-27
**Duration**: ~2 hours
**Focus**: Fix v4 slippage losses, create v5 with zero-slippage strategy

## Problem Identified

v4 paper trading showed consistent losses despite entering at "profitable" spreads:
- Combined ask = 0.99 (1% theoretical margin)
- Trade size = $350
- **Actual result: -$8.49 loss per trade**

Root cause: Order book slippage. Best ask at 0.99 only has ~$10-30 liquidity. Filling $350 eats through multiple levels, pushing effective price to 1.02-1.03.

## Solution: v5 Liquidity-First Stacking

**Core principle**: Only trade what's available at BEST ask price (zero slippage by design).

Key changes:
1. **No minimum trade size** - take $5, $10, whatever exists
2. **Only best-level liquidity** - never eat into order book
3. **Stack trades** - up to 10 small trades per coin per window
4. **No cooldown** - trade as fast as liquidity appears

## Backtest Comparison

Using same opportunity data:

| Metric | v4 | v5 |
|--------|----|----|
| Trades | 8 | 18 |
| Winners | 1 | **18** |
| Win Rate | 12.5% | **100%** |
| PnL | **-$53.63** | **+$2.59** |

## Implementation Details

### Config Changes
```python
max_trade_size: 100.0       # Capped by liquidity anyway
min_margin_to_trade: 0.005  # 0.5% minimum
gas_cost_per_tx: 0.003      # Realistic Polygon
max_stack_per_window: 10    # Multiple trades allowed
min_seconds_between_stack: 0.0  # No cooldown
```

### Multi-Instance Support
```bash
# Run different strategies in parallel:
python monitor_15m_v5.py --instance v5 --fresh
python monitor_15m_v5.py --instance aggressive --min-margin 0.002 --max-size 500
```

Each instance gets isolated paper trading files.

## Gas Cost Analysis

Researched actual Polygon gas costs:
- Simple transfer: $0.0003-0.0008
- CLOB order: ~$0.002-0.004
- **Configured**: $0.003/tx (conservative-realistic)
- Break-even at 1% margin: $0.60 trade size

## Running Instances

1. **v5** (conservative): min-margin 0.5%, max-size $100
2. **aggressive**: min-margin 0.2%, max-size $500

## Files Changed

- `scripts/monitor_15m_v5.py` - New monitor with stacking strategy
- `docs/CURRENT_STATE.md` - Updated documentation

## Git Commits

```
5d49662 feat: add monitor v5 with liquidity-first stacking strategy
734271c docs: update CURRENT_STATE for v5 monitor
```

## Next Steps

1. Monitor v5 and aggressive instances overnight
2. Compare actual performance between strategies
3. Consider adding more aggressive instance (0.1% margin?)
4. Analyze which coins have best liquidity at best price
