# Project Context — Gabagool22 Spread Arbitrage Bot

**Read this entire document before editing any code.** It explains what we are building, why it works, and — most importantly — the specific mistakes that have already been made so you do not repeat them. This project has a history of plausible-looking changes that broke the system in subtle ways. The bar here is correctness you can prove, not changes that look right.

---

## 1. The one-sentence mission

We are building an exact behavioral replica of a Polymarket trader called **gabagool22**, who runs a **pure market-neutral spread arbitrage** strategy on 15-minute crypto "up or down" prediction markets. The bot must never predict price direction. It buys both sides of a market when their combined price is below $1.00, merges the matched pairs back into $1.00 of USDC for a small risk-free margin, and recycles that capital into the next market — continuously, at high velocity.

If you find yourself adding anything about predicting whether BTC will go up or down, stop. That is the opposite of this strategy.

---

## 2. What gabagool22 actually does (the ground truth)

This is derived from forensic analysis of 141,658 of his real trades over his first ~3.8 days, plus his public portfolio page. These numbers are the reference target. Every design decision traces back to them.

**His results:**
- Deposited about **$986** on day one (two deposits: $5 + $981).
- Total all-time deposits: **$54,694.91**. Total all-time profit: **$868,862.52** (a 1207% return).
- Win rate: **83%**. Total positions: **57,206**.

**How he traded in the first 3 days:**
- Traded **916 unique markets** (BTC and ETH, every 15-minute window).
- Generated **$509,868 of buy volume** — from roughly $986 of working capital. That means he recycled his capital about **517 times**.
- Bought **both Up and Down** in every market. Never sold. Never used market/FOK orders. Only passive GTC limit orders.
- Average combined entry price (Up + Down per pair): **$0.9715**. He paid ~97¢ for a pair worth $1.00 and kept the difference.
- Margin per merged pair: **~2 to 3.5 cents**. Tiny per trade. The profit comes entirely from doing it 500,000+ times.
- Revenue split: **97.7% from MERGE**, 1.4% from REDEEM (winning leftover shares at resolution).

**The mechanism, stated plainly:** Post passive bids on both sides of every active market across a wide price ladder. When both sides fill at a combined price below $1.00, immediately merge the matched pairs into $1.00 USDC each. That USDC is available again within minutes and funds the next market's pairs. Unmatched ("naked") leftover shares are left to settle at market resolution — if they win, that is a bonus; the business is the merge spread, not the resolution outcome.

---

## 3. Why this works (the economics you must internalize)

Three properties make this profitable, and all three must hold or the strategy breaks:

**(a) It is market-neutral.** Every merged pair is a guaranteed win: you bought a Yes+No set for less than $1.00 and merged it for exactly $1.00. You do not care which way the market moves. The 83% win rate is mostly the merged pairs being guaranteed winners; the minority of naked leftover shares are a near coin-flip at resolution.

**(b) The edge is tiny, so velocity is everything.** A 3-cent margin on one pair is nothing. A 3-cent margin captured across half a million dollars of recycled flow is the whole business. The single most important architectural property of this bot is **capital velocity** — the same dollar must be spent, merged back, and re-spent as many times as possible. gabagool did ~517 round-trips in 3.8 days. A naive design that deploys capital once and waits for the market to resolve does maybe 1-2 round-trips per window — roughly 100x slower, which means ~100x less profit.

**(c) The merge must work.** The merge is the only thing that converts a held pair back into spendable cash and locks in the margin. If the merge breaks, you are not running an arbitrage — you are sitting on inventory hoping markets resolve in your favor, which is the losing directional bet this whole project exists to avoid.

---

## 4. What this is NOT (misconceptions that have already burned us)

These are real mistakes that were made earlier in this project. Do not make them again.

- **It is NOT directional trading.** The original version of this bot had a "spike detector," a "sniper," and a momentum "signal" that built a directional lean. It lost money. All of that was deleted. Do not reintroduce any price-direction logic.
- **It is NOT a wait-for-settlement strategy.** An earlier architecture deployed a fixed amount per "cycle" and waited 45+ seconds for the balance to settle before redeploying. This throttled velocity to near zero and made the strategy pointless. Capital must redeploy the instant a merge returns USDC, without waiting for prior markets' naked shares to resolve.
- **It does NOT enforce symmetric position sizing.** You post the same ladder on both sides and let fills happen naturally. Markets move, so one side will fill more than the other — that imbalance is fine. You merge the matched minimum and let the remainder settle. Forcing equal share counts just halves your fill rate.
- **It does NOT use a sparse ladder.** gabagool fills across the full price range. The ladder is 15 rungs from $0.10 to $0.80. A "simplified 3-rung ladder" was proposed once; it would miss most of the fills and is wrong.
- **It does NOT rely on market resolution for profit.** Resolution payouts are 1.4% of the revenue. The merge is 97.7%. Anyone who describes the bot as "relying on guaranteed resolution payouts" has misunderstood it.

---

## 5. Architecture overview

The runtime dependency chain:

```
main.py
  └── GabagoolBot (initializes components)
        └── SpreadFarmerStrategy            ← top-level strategy
              ├── WindowManager             ← continuous discovery + per-market loops
              │     └── MakerLoop (×N)      ← one per active market window
              │           └── MergeEngine   ← gasless on-chain merge
              ├── AutoRedeemer (background) ← claims winning naked shares post-resolution
              └── CapitalManager            ← allocation + realized-PnL stop-loss
        └── TradingBot (bot.py)             ← order execution, balance, get_spread
        └── GammaClient                     ← market discovery
```

**Component responsibilities:**

- **`main.py` / `GabagoolBot`** — process entry point and component wiring. Sets up the event loop (uvloop), loads config, constructs the strategy.

- **`strategies/spread_farmer.py` (`SpreadFarmerStrategy`)** — the top-level strategy. Launches the `WindowManager` discovery loop and the `AutoRedeemer` as a concurrent background task. This replaced the deleted `snipe_maker.py`.

- **`src/window_manager.py` (`WindowManager`)** — runs a single flat, continuous discovery loop. Each tick: checks the global stop-loss, cleans up finished sessions, discovers new active markets via `GammaClient.get_all_active_markets`, and spawns a `MakerLoop` for each new market up to `MAX_CONCURRENT_SESSIONS`. Markets overlap seamlessly; as merges free capital, the next tick can fund a new window. It must NOT have a cycle-based "wait for all windows to close" structure.

- **`src/maker_loop.py` (`MakerLoop`)** — the per-market engine. State machine: `FARMING → HOLD → DONE`. In FARMING it fetches both tokens' bids (via `bot.get_spread`), posts a 15-rung GTC ladder on both sides sequentially (80ms apart to avoid rate limits), reconciles fills, calls the merge engine when matched pairs exist, and cancels stale orders. At T-60s it enters HOLD: cancel all orders, force-merge remaining pairs. Then DONE. There are no SNIPING/COOLDOWN/LIQUIDATION states — those were deleted.

- **`src/merge_engine.py` (`MergeEngine`)** — performs the gasless on-chain merge via the Polymarket Unified SDK (the merge is submitted to a relayer and mined; you'll see "GASLESS MERGE MINED" with a tx hash in the logs). It includes a clamp-and-retry: when the SDK rejects with "Requested merge amount X exceeds the maximum mergeable amount Y" (because in-memory share counts run ahead of on-chain settlement), it parses Y, applies a 1% haircut, and retries. On success it credits proceeds toward the capital tracker so redeployment isn't throttled by the balance cache.

- **`src/capital_manager.py` (`CapitalManager`)** — continuous capital allocation (no isolated cycles). Computes per-window caps from the live balance, and runs the realized-PnL stop-loss. See Section 7 — this is the subtlest and most bug-prone part of the system.

- **`src/bot.py` (`TradingBot`)** — low-level execution: `place_order`, `get_spread`, balance reads with a 30-second cache (`_cached_balance_micro`, `_refresh_balance`), and the real affordability check (`_can_afford`). This is the only true backstop on overspending.

- **`src/gamma_client.py` (`GammaClient`)** — discovers active markets. The `Market` dataclass fields are: `id`, `condition_id`, `slug`, `question`, `description`, `yes_token_id`, `no_token_id`, `end_date`, `active`, `closed`, `resolved`, `volume`, `category`. Note there is **no** `resolution_time` field and **no** `asset` field — referencing those was a past crash bug. Use `slug` to distinguish window durations.

- **`src/auto_redeem.py` (`AutoRedeemer`)** — polls the Polymarket Data API for redeemable (winning) positions after markets resolve and claims them on-chain, feeding the payouts back into the capital tracker. With continuous flow this is load-bearing: without it, winning leftover shares stay unclaimed and that capital is frozen out of the recycling pool.

**Deleted files (do not resurrect):** `src/spike_detector.py`, `src/sniper.py`, `src/price_feed.py`, `src/websocket_client.py`, `strategies/snipe_maker.py`. These were all part of the directional/momentum design.

---

## 6. The trading loop in detail

For each active 15-minute BTC/ETH market:

```
FARMING (until T-60s before window close):
  every ~15s:
    1. Fetch Yes-bid and No-bid from the CLOB order book (bot.get_spread on each token).
    2. Post GTC limit orders at 15 rungs ($0.10 → $0.80) on the Yes token,
       sequentially, 80ms between rungs.
    3. Post the same 15-rung ladder on the No token, same pacing.
    4. Per-rung cost gate: before posting a rung at price P on one side, if the
       OTHER side already has fills, skip the rung when (P + other_side_avg_cost)
       would exceed MAX_COMBINED_COST_GATE (0.97). This prevents adding fills that
       turn a pair into a guaranteed loser.
    5. Capital-at-work gate: stop posting when held inventory cost basis + value of
       resting orders >= the window's capital cap. (NOT gross lifetime spend — see §7.)
    6. Reconcile fills (orders no longer open = filled).
    7. If min(up_shares, down_shares) >= MIN_MERGE_SHARES, call merge_engine.try_merge().
    8. Cancel orders older than the max age.

HOLD (T-60s → T-0):
    Cancel all open orders. Force-merge all remaining matched pairs.

DONE:
    Window expired. Naked leftover shares settle on-chain at resolution;
    AutoRedeemer claims the winners.
```

The two gates in steps 4 and 5 are both essential and were both bugs at some point:
- The **per-rung cost gate** must be a *pre-check before posting each rung*, not a post-hoc check on the running average. Otherwise the first flush of fills on both sides can lock in pairs costing well over $1.00.
- The **capital-at-work gate** must measure capital *currently held* (cost basis of shares you still hold + value of resting orders), NOT gross lifetime spend. If it uses gross spend, the window freezes permanently after one fill-and-merge pass and never recycles — which silently kills the entire strategy.

---

## 7. The accounting model (read this twice — it has caused repeated bugs)

The trickiest part of this project is the bookkeeping that drives two decisions: how much capital is available to deploy, and when to trip the stop-loss. Getting it wrong does not crash loudly — it makes the bot believe it has more money and more profit than it does, which is the most dangerous failure mode for live funds.

There are four quantities per market:

1. **Gross spent** = total USD paid for all shares (Up cost + Down cost).
2. **Merged returns** = USD received from merging matched pairs ($1.00 per pair).
3. **Merged cost basis** = the portion of gross spent attributable to the shares that got merged (pairs × combined price per pair).
4. **Naked cost basis** = gross spent minus merged cost basis = the cost of the unmatched leftover shares.

**Realized PnL** (what the stop-loss watches) must be:

```
realized_pnl = (merged_returns + redeemed_payouts) - (merged_cost_basis + naked_cost_basis_of_RESOLVED_legs)
```

The rules that keep this honest:

- **Book each cost basis only against its matching return, never ahead of it.**
  - At window close: book the merged portion only — `gross = merged_cost_basis`, `return = merged_returns`. This nets a small positive and never dips.
  - At on-chain resolution of the naked leg: book `naked_cost_basis` together with its outcome. Winner → also book the redeemed payout. **Loser → book the naked cost with a zero payout.** Both outcomes must be booked, or losses become invisible.
- **Why this matters:** if you book the full gross at window close but only book payouts later (when redemptions clear), every imbalanced window shows a temporary phantom loss equal to the naked cost — and several windows resolving together can false-trip the stop-loss on inventory that is about to pay out. Conversely, if you only ever book winning redemptions and never book losing naked shares, realized PnL is permanently overstated and the stop-loss goes blind to real losses. Both errors have happened. The fix is the pairing rule above.

**Live balance** (what drives deployment sizing) must not double-count merge proceeds:

- The merge engine may optimistically credit freshly-merged USDC so redeployment isn't throttled by the 30-second balance cache.
- **But that optimistic credit must be reconciled away when a real on-chain balance refresh happens**, because the refreshed balance already includes the settled proceeds. An accumulator that only ever increments and is never reset will inflate the balance without bound, causing the bot to over-deploy cash it does not have. (This was a live bug: a `pending_merge_proceeds` accumulator that `_refresh_balance` never reset.)

If you touch any of this, you must prove it with arithmetic — see Section 10.

---

## 8. Configuration

Config lives in `config/production.yaml` (live) and `config/default.yaml` (defaults). Current intended settings:

| Setting | Value | Meaning |
|---|---|---|
| `session_capital_usd` | 1000.0 | Working capital pool (gabagool used ~$986). |
| `auto_compound_pct` | 0.0 | Fixed pool, does not scale with profit yet ("training wheels"). |
| `max_concurrent_arbitrages` | 2 | **Currently reduced to 2 for validation.** Target is ~6 (gabagool's ratio) only after the recycle loop is proven. |
| `max_combined_cost` / `MAX_COMBINED_COST_GATE` | 0.97 | Refuse pairs whose combined entry would exceed 97¢. gabagool averaged 0.9715. |
| `LADDER_STEPS` | 15 rungs, $0.10–$0.80 | Matches gabagool's observed fill distribution. |
| ladder posting | sequential, 80ms/rung | Avoids the HTTP 425 "service not ready" flood from concurrent blasts. |
| realized-PnL stop-loss | −$150 | Shuts down on a genuine realized loss (see §7 for what "realized" must mean). |

Sizing reality: a full 15-rung ladder on both sides costs roughly $100. So $1000 supports a handful of windows farming properly. Do **not** run 6 concurrent windows on $200 — you get token fills and misleading "it isn't crashing" signals. Either run small-and-honest (low capital, 2 windows) to prove the loop, or match gabagool's ratio (~$1000, ~6 windows). The current config is the validation setting: $1000 with concurrency held at 2.

---

## 9. Project history and current known bugs

**History (so you understand the scars):**
1. The bot started as a directional momentum trader (spike detector + sniper + signal). It lost ~90% of a test wallet in a day. All directional logic was deleted.
2. It was rebuilt as pure spread, but had crash bugs (calling nonexistent methods like `bot.get_bids` and `gamma.get_active_windows`, referencing a phantom `market.resolution_time`) that silently prevented it from placing any orders. Those are fixed.
3. The architecture was then converted from cycle-based (deploy, wait for settlement) to continuous-flow (recycle merged capital instantly). The capital-at-work gate and the merge-proceeds credit were added. Correct in principle.
4. The accounting that powers the stop-loss and balance is the current problem area.

**Known open bugs as of this writing (verify before assuming fixed):**
- **Redemption call crashes.** `record_redemption` was given a second required parameter (`naked_cost_basis`) but the call site still passes one argument → `TypeError` when a winning ticket is claimed. The data flow to supply the cost basis (a `conditionId → naked_cost_basis` lookup the redeemer can read) was never wired. Fix the data flow, not just the call signature.
- **Losing naked shares are invisible.** Naked cost basis is only booked inside the winner-only redemption path, so the cost of losing leftover shares (≈17% of naked inventory) never enters realized PnL. The stop-loss is effectively blind to real losses. Fix: book naked cost basis at on-chain resolution for both winners and losers.
- **Merge proceeds double-counted.** `pending_merge_proceeds` increments on every merge and is never reset; `_refresh_balance` doesn't reconcile it. The live balance inflates without bound, driving over-deployment. Fix: reset the accumulator at the end of a successful real balance refresh.

All three bugs push the same direction: the bot overestimates cash and profit. Until they are fixed and reconciled, do not run the bot live.

---

## 10. How to work on this codebase (hard rules)

1. **Never reintroduce direction.** No price prediction, no momentum, no signal, no sniper. Market-neutral only.
2. **Never throttle velocity.** Merged capital redeploys immediately. Do not gate redeployment on prior markets' settlement.
3. **Protect the merge.** It is 97.7% of the revenue. Do not refactor it casually. It currently works (gasless via Unified SDK) — if you change it, prove merges still mine on-chain.
4. **Never trust in-memory share counts for merge amounts.** They run ahead of on-chain settlement. Always clamp the merge amount to what the chain reports (the clamp-and-retry exists for this).
5. **Accounting changes require arithmetic proof.** If you touch realized-PnL or balance logic, run a dry-run that prints the four quantities (gross, merged returns, merged cost basis, naked cost basis), plus realized PnL and live balance, after a simulated buy → merge → resolution cycle, and show they reconcile against a hand calculation. "Watch the logs" is not verification.
6. **Do not declare success without evidence.** This project has a pattern of "fixed, deployed, watch for it" followed by the fix not working. Before claiming a behavior works, demonstrate it. The single most important unproven behavior is the recycle loop: a log showing, within one window, `fill → merge → freed capital → new fill in the same window`. Until that sequence is observed with real arithmetic behind it, the engine is unproven.
7. **Dry-run before live, always.** `--dry-run` mode exists. Use it. Note that dry-run uses hardcoded bids (0.30/0.70) and swallows discovery errors, so it can mask live-only bugs — which is exactly why arithmetic reconciliation matters more than "it ran without crashing."
8. **One change at a time, verified.** Roughly half the fixes in this project's history introduced a regression. Small, checked changes beat big confident rewrites.

---

## 11. Definition of done (what "working" actually means)

The bot is not "done" because it runs without crashing. It is working when, with real money at small scale, you can show all of:

1. **Orders post on both sides** with no meaningful 425 error rate.
2. **The recycle loop fires**: capital is spent, merged back, and re-spent within a single window — observed in logs, backed by arithmetic.
3. **The combined fill price is near 0.97** (gabagool's benchmark). If your real fills average 0.99+, you are losing the margin to adverse selection and the strategy does not work at your fill quality — this single number is the truth test.
4. **Merge capture is high**: the ratio of merged USDC to total invested per window is large (gabagool's was high enough that merges were 97.7% of revenue). If most of your fills end up as naked leftover, you are running a directional bet on the remainder, not an arbitrage.
5. **Realized PnL reconciles** to a hand calculation and the wallet balance actually grows across many cycles.

Number 5 is the only metric that ultimately matters. Getting the bot to run was the engineering problem. Whether the strategy is profitable at our scale and fill quality is the empirical question, and it is still unanswered.

---

## 12. Glossary (Polymarket / CTF terms)

- **Condition / market** — a single binary prediction market (e.g., "BTC up or down, 5:00–5:15pm"). Identified by a `condition_id`.
- **Outcome tokens (Yes / No, here "Up" / "Down")** — each market has exactly two ERC-1155 outcome tokens. A winning token pays $1.00 at resolution; a losing token pays $0.00.
- **Pair / set** — one Up token + one Down token. A complete set is always worth exactly $1.00.
- **MERGE** — combining a matched Up+Down pair back into $1.00 of USDC. This is an on-chain operation. It is how the spread margin is realized and how capital is recycled. **This is the core of the business.**
- **REDEEM** — claiming $1.00 for a winning outcome token after the market resolves on-chain. Applies only to naked (unmatched) leftover shares. A minor revenue source.
- **Naked / leftover shares** — outcome tokens with no matching opposite token to merge against (because one side filled more than the other). They are held until resolution.
- **Maker / taker** — a maker posts a resting limit order and waits to be filled; a taker crosses the spread to fill immediately. gabagool is a pure maker. He gets filled when takers come to his resting bids.
- **Combined price** — the sum of the average Up price and average Down price per pair. Must be below $1.00 for the merge to be profitable. Target ~0.97.
- **Gasless merge / relayer** — the Unified SDK submits the merge transaction through a relayer so the bot doesn't pay gas directly; the relayer mines it on-chain.
- **CLOB** — Polymarket's Central Limit Order Book, where limit orders rest and match.
- **Window** — a single 15-minute market's lifetime, from open to resolution.
