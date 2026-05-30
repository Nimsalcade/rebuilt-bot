# Gabagool (PolyBotTOP)

**Automated Delta-Neutral Spread Arbitrage Bot for Polymarket CLOB**

*Version 2.0 | Built for Polymarket CLOB | Polygon Network*

---

## Overview

Gabagool is an exact behavioral replica of a highly successful Polymarket trader (`gabagool22`), executing a **pure market-neutral spread arbitrage** strategy on short-duration (15m) crypto "up or down" prediction markets. 

The bot is entirely agnostic to price direction. It buys both sides of a market passively using GTC limit orders when their combined price is below $1.00. Once filled, it instantly merges the matched pairs back into exactly $1.00 of USDC on-chain, securing a small risk-free margin (the spread). It then rapidly recycles that unlocked capital into the next market — running a high-velocity capital loop.

---

## The Core Edge

The strategy relies on three critical properties:
1. **Market Neutrality**: The bot profits from the spread between the UP and DOWN order books, not from correctly guessing if BTC will go up or down.
2. **Capital Velocity**: Margins per merged pair are tiny (2-3.5¢). The bot generates alpha by completing hundreds of round trips per day (buy -> merge -> recycle).
3. **Inventory Balance (Matched Fraction)**: The bot actively balances its inventory (using a Max Lean Cap). If one side runs away (e.g. 20% heavier), it halts posting on that side. This ensures that ~90%+ of capital converts into risk-free matched pairs, preventing the bot from holding massive, negative-EV naked directional positions.

---

## How It Works

```
Buy UP  @ $0.48 avg   ──┐
                         ├──▶  Combined cost $0.97  ──▶  Instant Merge (On-chain) ──▶  +$0.03 profit
Buy DOWN @ $0.49 avg  ──┘         (< $1.00 ✅)             (Yields $1.00 USDC)           per pair
```

1. **Farming**: The bot posts a wide, 15-rung price ladder ($0.10–$0.80) on both sides of active 15m crypto markets.
2. **Merging**: When the CLOB fills orders on both sides, the `MergeEngine` detects the overlapping shares and triggers a gasless Polymarket SDK merge.
3. **Recycling**: The merged $1.00 USDC is instantly credited back to the bot's working capital, ready to fund the next limit order.
4. **Redeeming**: Any unmatched "naked" shares (leftover imbalance) are held until the market expires. The `AutoRedeemer` runs in the background to claim the $1.00 payouts on any naked shares that happened to win.

---

## Project Structure

```
gabagool-main/
├── src/                  # Core source code
│   ├── main.py           # Process entry point
│   ├── window_manager.py # Discovers new markets, spawns MakerLoops
│   ├── maker_loop.py     # State machine per market (Farming -> Hold -> Done)
│   ├── bot.py            # TradingBot: Polymarket CLOB client, execution, balance cache
│   ├── capital_manager.py# Allocates working capital, enforces Realized PnL Stop-Loss
│   ├── auto_redeem.py    # Background task claiming winning naked shares
│   ├── merge_engine.py   # Handles on-chain gasless merges
│   └── gamma_client.py   # Gamma API client for market discovery
├── strategies/           # Strategy modules
│   └── spread_farmer.py  # Top-level strategy orchestrator
├── config/
│   ├── default.yaml      # Default parameters
│   └── production.yaml   # Live trading config (Gate limits, ladders)
├── tests/                # Test suite
│   └── test_accounting_reconciliation.py # Crucial arithmetic proof test
├── Makefile              # Run commands
└── requirements.txt      # Dependencies
```

---

## Quick Start

```bash
# 1. Clone & setup
git clone https://github.com/Nimsalcade/GAANG.git gabagool
cd gabagool
make setup

# 2. Configure credentials
cp config/.env.example config/.env
nano config/.env
# Add your POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS

# 3. Dry run (paper trading — no real funds)
make run

# 4. Verify Accounting (CRITICAL BEFORE LIVE RUNS)
make reconcile

# 5. Live trading
make run-live
```

---

## Hard Rules for Development

This project has a history of subtle accounting and strategy bugs. **Read `PROJECT_CONTEXT.md` before making any changes.**
1. **Never reintroduce direction**: No momentum signals, no snipers, no predicting prices.
2. **Never throttle velocity**: Merged capital must redeploy immediately.
3. **Arithmetic proofs required**: If you touch capital management, balance tracking, or PnL logic, you must prove your changes mathematically using `make reconcile`. "Watch the logs" is not verification.
4. **Dry-run first, always**.

---

## Risk Controls

- **Per-Rung Cost Gate**: Refuses to post a rung if the combined cost (Price + Opposite Side Average) exceeds `$0.97`.
- **Inventory Balance Cap**: Halts posting on a side if it becomes 20% and >15 shares heavier than the opposite side (prevents directional gambling).
- **Capital-at-Work Cap**: Limits the amount of active deployed capital (resting limit value + held shares) per window.
- **Realized PnL Stop-Loss**: Shuts down the bot globally if the *realized* PnL (accounting for both won and lost naked shares) hits a strict floor.

---

## Security

- **Never commit private keys** — use environment variables only (`config/.env`).
- Ensure the account is a new Polymarket **Deposit Wallet** (`signature_type: 3` / `POLY_1271`).

---

## License

MIT License
