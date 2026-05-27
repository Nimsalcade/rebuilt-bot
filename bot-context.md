# Gabagool (PolyBotTOP / GAANG) - Project Context

> [!IMPORTANT]
> This document serves as the master context for future AI agents interacting with the Gabagool codebase. It summarizes the bot's architecture, strategy, and the successful live-production validation metrics.

## 1. Project Overview
Gabagool is a high-frequency, delta-neutral market-making and latency-arbitrage bot built for the **Polymarket Central Limit Order Book (CLOB)**. It specifically targets short-duration (5m, 15m, 1h) cryptocurrency "Up or Down" prediction markets (BTC, ETH). 

The bot is designed to be fully autonomous, recycling capital as markets resolve while strictly adhering to hardcoded risk limits.

## 2. What Was Implemented & Validated
During the most recent deployment session, the bot was transitioned from a local paper-trading simulation to a live production environment on a Vultr VPS (`216.128.178.26`).

**Key Achievements:**
*   **Live Strategy Validation:** The bot proved highly profitable in live conditions, turning an initial **$102.43** deposit into **$459.59** (+348% ROI) in roughly 60 minutes.
*   **Hyper-Compounding:** Validated the "capital recycling loop." As 15-minute markets settle on the blockchain, the bot instantly re-deploys the unlocked USDC cash into the next active window.
*   **Latency Execution:** Achieved global snipe burst latencies (hitting up to 4 markets simultaneously) between **154ms and 737ms** on the live Gamma API.
*   **Risk Mitigation:** The `max_total_exposure` safety limit successfully throttled the bot, proving that profits can be safely "locked" as idle cash if the exposure limit is intentionally kept lower than the total wallet balance.

## 3. Bot Behavior & Strategy
The bot utilizes two parallel strategies to capture edge:

1.  **Passive Market Making (`MakerLoop`):**
    *   Lays down limit order "ladders" on both the UP and DOWN sides of a market.
    *   Targets a strict `max_combined_cost` (e.g., $0.96). It seeks to buy UP at 45¢ and DOWN at 51¢. Since one side is mathematically guaranteed to resolve to $1.00, paying 96¢ guarantees a 4% risk-free arbitrage.
2.  **Aggressive Latency Arbitrage (`GlobalSniperEngine`):**
    *   Maintains zero-latency WebSocket connections to Binance Futures (`aggTrade`).
    *   Monitors for sudden price spikes (e.g., > 0.02% in a 5-second rolling window).
    *   If a spike occurs, it instantly crosses the Polymarket spread to sweep resting liquidity before human traders or slower AMMs can update their prices.

## 4. Core Codebase Architecture
The codebase is located in `/root/gabagool-main/` (VPS) and tracked via the `GAANG` GitHub repository.

*   `config/production.yaml`: The strict rulebook. Defines `max_total_exposure`, `max_combined_cost` (0.96), and maximum concurrent windows.
*   `src/main.py`: The orchestrator. Initializes the global state and starts the window manager.
*   `src/window_manager.py`: Autonomously queries the Polymarket API to discover new 5m/15m/1h crypto markets, tracks their expiration clocks, and spins up `MakerLoop` threads for each.
*   `src/maker_loop.py`: The state-machine for an individual market window. Transitions between `FARMING`, `HOLD`, and `EXPIRED` based on time to resolution.
*   `src/sniper.py`: Handles the high-speed execution logic and order payload construction for the `GlobalSniperEngine`.
*   `src/trading_bot.py`: The interface to Polymarket's Gamma CLOB. Handles cryptographic transaction signing, tracking active/resting orders, and canceling unfilled orders.
*   `src/risk_manager.py`: Intercepts every order before it is sent to the API. Rejects orders if they violate the `max_total_exposure` ceiling or fail the profit margin check.

## 5. Known Issues / Pending Work
> [!WARNING]
> Future agents should address the following bugs before scaling capital limits.

1.  **Deque Mutated During Iteration:**
    *   *Symptom:* Logs occasionally throw `Unexpected error placing order: deque mutated during iteration`.
    *   *Cause:* A Python threading/race condition in `trading_bot.py` or `maker_loop.py`. The bot attempts to iterate over a list of active orders (`collections.deque`) at the exact millisecond the WebSocket response thread modifies that same list to mark an order as filled/canceled.
    *   *Fix Required:* Implement a `threading.Lock()` around the deque operations or iterate over a `.copy()`.
2.  **API 400 Errors (Insufficient Balance):**
    *   *Symptom:* When the bot deploys 100% of its allowed capital, it spams the API with orders that get rejected with a 400 error (`not enough balance`).
    *   *Cause:* The local balance tracker gets slightly out of sync with the live Polymarket wallet by fractions of a penny (often due to fee deductions on partial fills). 
    *   *Fix Required:* Add a strict local cash buffer check before attempting API requests when the free balance drops below $1.00.
