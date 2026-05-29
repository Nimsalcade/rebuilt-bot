# Pure Spread Arbitrage — Corrected Implementation PRD
**Version:** 2.0 (authoritative — supersedes your draft)  
**Based on:** Forensic analysis of 141,658 gabagool22 trades + full codebase audit + live log analysis  
**Status of deployed bot vs uploaded codebase:** The uploaded snapshot is **stale**. The your already deployed a Unified SDK merge that is partially working. This PRD accounts for that.

---

## Part 0 — Corrections to the your Original Plan

Read this section first. Do not proceed until you have accepted all corrections.

### Correction 1 — The two bugs are NOT fixed (your claim is wrong)

The your stated: *"both of those errors belonged to the old poly_merger.py system… permanently fixed."*

This is incorrect. Here is the current state of each bug:

**Bug A — "Expected at most two positions, got 14"**  
This error came from `merge_engine.py`'s call `clob.merge_positions(condition_id, amount)` on a wallet with 14 open position tokens (7 markets × 2 sides). The **uploaded codebase still has this unfixed call**. The your replaced poly_merger.py with merge_engine.py but did not fix the SDK call arguments. The live logs confirm merge is now partially working via a Unified SDK gasless path the your deployed separately — but there is still a remaining merge bug (see Correction 2).

**Bug B — "'BotConfig' object has no attribute 'rpc_url'"**  
`BotConfig` in `src/bot.py` has no `rpc_url` field. `Config` (in `src/config.py`) does, but `TradingBot.config` is typed as `BotConfig`. Any code path that does `self.bot.config.rpc_url` on a `TradingBot` instance will raise this error. The uploaded codebase still has `BotConfig` with no `rpc_url`. **However, for the pure spread strategy this is irrelevant** — we are removing ALL Web3/RPC fallback code. The fix is deletion, not a patch.

### Correction 2 — There is a NEW remaining merge bug the your has not acknowledged

The live logs show the deployed Unified SDK merge is working but failing ~60% of attempts with:

```
Unified SDK merge exception: Requested merge amount 81000000 exceeds 
the maximum mergeable amount 71315500 for condition 0x12d9...
```

**Root cause (diagnosed from logs):** The bot calculates `mergeable = min(up_shares, down_shares)` from its in-memory `WindowFillSummary`. Fill detection works by polling `get_open_orders()` — when an order disappears, it is marked locally as filled. But on Polymarket, token minting takes 2–5 seconds after the order settles. The bot calls merge immediately after fill detection, before the tokens are actually on-chain. The Polymarket relayer then rejects with the exact delta being short (~9.68 shares per attempt = one ladder rung's worth of unsettled fills).

**Fix:** Parse the maximum mergeable amount from the error message and retry with that clamped value (see Part 3, Bug Fix #1).

### Correction 3 — The 3-rung ladder is far too sparse

The your proposed: *"a simplified, highly concentrated ladder (e.g. 3 rungs: $0.15, $0.35, $0.45)"*

This is wrong. Gabagool22's actual fill distribution from 141,658 trades spans $0.01–$0.98, with heavy volume across ALL price levels. A 3-rung ladder at $0.15/$0.35/$0.45 would:
- Miss ~70% of the price range where gabagool22 gets fills
- Generate far fewer fills per window, making merge amounts too small to be worth the gas
- Fail to replicate his ~$230k in merge revenue that came from high volume across ALL rungs

**Correct approach:** 15 rungs from $0.10 to $0.80 in $0.05 increments, posted **sequentially with 80ms between each rung** to avoid the 425 flood. This matches gabagool22's observed fill distribution and fixes the rate-limit issue without sacrificing coverage.

### Correction 4 — "Symmetrical bidding" enforcement is wrong

The your proposed: *"enforce strictly equal position sizing on both sides."*

Gabagool22 does NOT enforce equal share counts. His data shows natural imbalances (e.g., 70 UP shares at avg $0.42, 87 DOWN shares at avg $0.38 — the market was trending UP so cheap DOWN fills outnumbered UP fills). This is fine — you merge `min(70, 87) = 70` pairs and let the remaining 17 naked DOWN shares settle. If DOWN wins, you get $17 back. If UP wins, the 17 shares go to zero, but you already captured the spread on 70 merged pairs.

Enforcing artificial equal sizing would stop posting the leading side prematurely, halving your fill rate for no benefit. **Do not add any symmetry enforcement.** Post the same price ladder on both sides and let fills happen naturally.

### Correction 5 — The cost gate needs a per-rung pre-check, not just a global post-hoc check

The existing `MAX_COMBINED_COST_GATE = 0.95` fires AFTER fills happen on both sides and their average exceeds 0.95. This allows individual pairs to cost $1.30+ before the gate stops anything. The first flush of fills on both sides at high prices is irreversible.

**Correct approach:** add a per-rung pre-check. Before posting UP at price P, skip that rung if `avg_down_cost > 0 AND (P + avg_down_cost > 0.97)`. Same for DOWN. This prevents contributing new fills that would worsen an already unprofitable average. Keep the global gate too, but the per-rung check does the real work.

### Correction 6 — The capital manager will stop the bot after every single losing cycle

`production.yaml` has `auto_compound_pct: 1.0`. With 100% auto-compounding, `session_capital_usd` equals the full wallet balance. The stop-loss then kills the session the moment `returned < session_capital` — i.e., on ANY loss whatsoever. Every cycle that has even $0.01 of net loss triggers shutdown. This is why the bot restarts constantly in the logs.

**Fix:** Set `auto_compound_pct: 0.0` initially. Use a fixed `session_capital_usd: 200.0`. Add a percentage-based stop-loss tolerance of 15% so the bot survives normal variance without shutting down.

### Correction 7 — The verification plan deploys directly to production without any dry-run gate

The your verification plan starts with: *"push the changes to the VPS and restart the bot."* This is not acceptable. Any new implementation must run in `dry_run: true` mode and pass quantitative checks before live money is risked. See Part 5 for the correct phased protocol.

---

## Part 1 — Gabagool22 Ground Truth (from 141,658-trade forensic analysis)

This is the reference behavior. Every implementation decision must trace back to it.

**What he does:**
- Buys BOTH Up AND Down in every active 15-minute market (BTC and ETH, all windows)
- Places GTC maker limit orders across the FULL price range: $0.01 to $0.98
- Average fill: ~9 shares at ~$0.48 = ~$4.32 USDC per fill
- Average combined price (Up + Down per pair): **$0.9651** — he buys pairs for $0.965 and merges for $1.00
- 36,889 fills per day across 239 unique markets
- Revenue: 97.7% from MERGE ($232,414), 1.4% from REDEEM ($1,029) on settled positions
- ROI on fully-closed markets: +20.7%

**What he does NOT do:**
- Never places SELL orders
- Never uses FOK or market orders  
- Never predicts price direction
- Never cancels-and-resnipes
- Never holds a Binance WebSocket feed

**The mechanism in one sentence:**  
Post passive GTC bids on both sides of every market. When both sides fill at a combined price below $1.00, merge the matched pairs immediately for risk-free profit. Let unmatched naked shares settle at expiry — if they win, that's a bonus.

---

## Part 2 — Target Architecture

The entire bot after this conversion has **one behavior loop**:

```
For each active 15-minute BTC/ETH market window:

  FARMING:
    Every 15 seconds:
      1. Fetch YES bid and NO bid from CLOB order book
      2. Post GTC limit orders at 15 rungs ($0.10→$0.80) on YES token,
         sequentially, 80ms apart
      3. Post GTC limit orders at 15 rungs ($0.10→$0.80) on NO token,
         sequentially, 80ms apart
      4. Skip any rung where per-rung cost gate would fire (see Part 3 Bug Fix #2)
      5. Detect fills (reconcile against get_open_orders())
      6. Merge: if min(up_shares, down_shares) >= 10, call merge_engine.try_merge()
      7. Cancel orders older than 15 seconds
    Repeat until T-60s before market close.

  HOLD (T-60s to T-0):
    Cancel all open orders.
    Force-merge all remaining matched pairs.
    Wait for window expiry.

  DONE:
    Naked shares settle on-chain at Polymarket resolution.
    No action needed — auto_redeem.py handles claiming winners.
```

**No spike detection. No sniping. No direction signal. No Binance WebSocket. No BurstSignal. No GlobalSniperEngine. No liquidation.**

---

## Part 3 — Critical Bug Fixes (implement in this order)

### Bug Fix #1 — Merge overestimation (HIGHEST PRIORITY — active in production now)

**File:** `src/merge_engine.py`

**Problem:** `mergeable = min(up_shares, down_shares)` uses in-memory tracking that is ~2–5s ahead of on-chain settlement. The Unified SDK rejects with `"Requested merge amount X exceeds the maximum mergeable amount Y"`.

**Fix:** Add a regex to parse Y from the error, apply a 1% safety haircut, and retry immediately:

```python
import re

_MAX_MERGEABLE_RE = re.compile(
    r'maximum mergeable amount\s+(\d+)', re.IGNORECASE
)

def _clamp_and_retry(self, error_msg: str, condition_id: str,
                     yes_token_id: str, no_token_id: str) -> Optional[float]:
    """
    Parse the actual maximum mergeable amount from a rejection error
    and return a safe retry amount. Returns None if unparseable.
    """
    m = _MAX_MERGEABLE_RE.search(error_msg)
    if not m:
        return None
    max_micro = int(m.group(1))
    safe_micro = int(max_micro * 0.99)          # 1% haircut
    safe_shares = safe_micro / 1_000_000
    if safe_shares < MIN_MERGE_SHARES:
        return None
    return safe_shares
```

In `_execute_merge_via_clob`, after catching the overestimation error, call `_clamp_and_retry` and make one additional attempt before giving up.

**Also fix in the same file:** Remove `SNIPER_INVENTORY_RESERVE = 15.0`. In the pure spread strategy there is no sniper and no reason to hold back shares. Remove the reserve entirely from the `try_merge` calculation:

```python
# Old (wrong for pure spread):
mergeable = max(0.0, min(up_shares, down_shares) - SNIPER_INVENTORY_RESERVE)

# New (correct):
mergeable = min(up_shares, down_shares)
```

### Bug Fix #2 — Per-rung combined cost gate

**File:** `src/maker_loop.py`

**Problem:** The existing gate `if combined > MAX_COMBINED_COST_GATE: return` fires after both sides have fills and the average crosses 0.95. But the first high-price fills on both sides can lock in a combined cost of $1.30+ before the gate triggers.

**Fix:** Add a per-rung check inside `_post_ladder_rung` before placing each order:

```python
async def _post_ladder_rung(self, token_id, side, price, active_orders, summary):
    # Per-rung pre-check: would this rung push the combined average over the gate?
    # Only applies once we have fills on the OTHER side.
    if side == "UP" and summary.down_gross_shares > 0:
        projected = price + summary.down_avg_cost
        if projected > MAX_COMBINED_COST_GATE:
            return False  # skip this rung — too expensive
    elif side == "DOWN" and summary.up_gross_shares > 0:
        projected = summary.up_avg_cost + price
        if projected > MAX_COMBINED_COST_GATE:
            return False  # skip this rung — too expensive
    
    # ... rest of existing rung posting logic
```

**Also:** Lower `MAX_COMBINED_COST_GATE` from `0.95` to `0.97`. gabagool22's average combined is 0.9651. A gate of 0.95 is so tight it would stop posting mid-window on profitable fills. The gate's purpose is to prevent pairs that are guaranteed losers (combined > $1.00), not to over-restrict profitable fills. Use `0.97` — this allows fills that are profitable after merge and blocks fills that are not.

### Bug Fix #3 — Fix the 425 rate-limit flood

**File:** `src/maker_loop.py`

**Problem:** `_post_ladder` fires all 15–17 rungs concurrently via `asyncio.gather`. With 4 concurrent markets × 2 sides × 17 rungs = 136 simultaneous HTTP POSTs every 15 seconds. Polymarket's CLOB returns `425 "service not ready"` on 94% of them.

**Fix:** Replace the `asyncio.gather` parallel blast with a sequential loop with 80ms delay:

```python
async def _post_ladder(self, token_id, side, active_orders, summary):
    """Post the full price ladder sequentially to avoid 425 rate-limit errors."""
    placed = 0
    for price in LADDER_STEPS:
        result = await self._post_ladder_rung(
            token_id, side, price, active_orders, summary
        )
        if result:
            placed += 1
        await asyncio.sleep(0.08)   # 80ms between rungs = ~1.2s for full ladder
    self.logger.debug(
        "Ladder posted: %s %s | %d/%d rungs",
        side, summary.market_id[:12], placed, len(LADDER_STEPS)
    )
```

The 80ms gap per rung × 15 rungs × 2 sides = 2.4 seconds per market per ladder refresh. With 4 concurrent markets, that's still only ~10 API calls per second total — well within rate limits.

### Bug Fix #4 — Capital manager stop-loss is too aggressive

**File:** `config/production.yaml`

**Problem:** `auto_compound_pct: 1.0` makes `session_capital = full_wallet_balance`. Any cycle that loses even $0.01 triggers the stop-loss and kills the session permanently. The bot restarts, runs one cycle, loses a few cents on a bad fill, shuts down again. This is why the logs show the bot restarting 26 times in one day.

**Fix (config change, not code change):**
```yaml
# production.yaml
gabagool:
  session_capital_usd: 200.0
  auto_compound_pct: 0.0        # CHANGE FROM 1.0 — use fixed session capital
  max_daily_drawdown_pct: 0.15  # Keep — 15% daily drawdown is the real circuit breaker
```

**Also add a percentage-based stop-loss tolerance in `capital_manager.py`:**
```python
# Add this constant at the top of capital_manager.py
STOP_LOSS_TOLERANCE_PCT = 0.15  # Allow up to 15% loss per cycle before stopping

# In start_cycle(), replace:
if returned < self.session_capital_usd:   # old: triggers on ANY loss

# With:
if returned < self.session_capital_usd * (1 - STOP_LOSS_TOLERANCE_PCT):  # new: only on >15% loss
```

---

## Part 4 — Complete File Inventory

### DELETE these files entirely

Remove all imports of these files first (search for their names across the codebase), then delete.

| File | Reason |
|------|--------|
| `src/spike_detector.py` | Only used for price-direction detection. Pure spread has no direction. |
| `src/sniper.py` | Cancel-and-snipe is incompatible with pure passive spread. |
| `src/price_feed.py` | Binance WebSocket feed. Only used to feed the spike detector. |
| `src/websocket_client.py` | WebSocket infrastructure used by price_feed.py. |
| `strategies/snipe_maker.py` | Top-level strategy wiring for the old snipe system. |

### REWRITE these files

Full rewrites, not patches. The existing implementations have too many snipe/signal assumptions baked in.

#### `strategies/spread_farmer.py` (NEW FILE — replaces snipe_maker.py)

This is the new top-level strategy entry point. Much simpler than snipe_maker.py:

```python
"""
SpreadFarmerStrategy — pure market-neutral spread arbitrage.

Wires together WindowManager (spread-only mode) and PaperTrader.
No price feeds. No spike detectors. No snipers.
"""

class SpreadFarmerStrategy:
    def __init__(self, bot, config, dry_run=False):
        self.window_manager = WindowManager(
            bot=bot,
            config=config,
            dry_run=dry_run,
            paper_trader=PaperTrader(...) if dry_run else None,
        )

    async def run(self):
        await self.window_manager.run_forever()
```

#### `src/maker_loop.py` (REWRITE)

Remove all of: `SNIPING` state, `COOLDOWN` state, `LIQUIDATION` state, `SnipeSlot` registration, `BurstSignal` handling, `burst_signal` parameter, `signal_engine` parameter, `sniper` parameter, `spike_detector` parameter, `signal_direction` tracking, `signal_confidence` tracking, `naked_payout` PnL calculation.

**Keep:** `FARMING` state, `HOLD` state, `DONE` state, the price ladder posting logic, fill reconciliation, `MergeEngine` call, capital gate checks.

**State machine after rewrite:**
```
FARMING → [T-60s before close] → HOLD → [window expired] → DONE
```

**New `run()` signature** (simpler — no snipe dependencies):
```python
async def run(
    self,
    market: Any,
    window_end: datetime,
    risk_manager: Any = None,
    stats_tracker: Any = None,
    db: Any = None,
) -> WindowFillSummary:
```

**New ladder steps** (15 rungs, matching gabagool22's observed distribution):
```python
LADDER_STEPS = [
    0.10, 0.15, 0.20,
    0.25, 0.30, 0.35,
    0.40, 0.45, 0.50,
    0.55, 0.60, 0.65,
    0.70, 0.75, 0.80,
]
```

**New `FARM_REFRESH_S = 15.0`** (was 10.0 — give more time for fills to settle before merge)

**Remove from `WindowFillSummary`:**
- `signal_direction`
- `signal_confidence`
- `signal_fired_at_s`
- `snipes_fired`
- `snipe_latency_avg_ms`
- `lock` field (asyncio.Lock — only needed for concurrent snipe writes)

**The PnL calculation at window close must change:**
```python
# Old (wrong — included directional bet payout):
naked_payout = float(naked_sh) if signal_direction == naked_side else 0.0
rough_net = (merged + naked_payout) - cost

# New (correct — naked shares are unknowable until settlement):
rough_net = summary.merged_usdc - summary.total_invested
# Note: This will be negative until settlement. That is expected and correct.
# The bot's actual profit will be confirmed by capital_manager balance checks.
```

#### `src/window_manager.py` (REWRITE)

Remove all of: `GlobalSniperEngine`, `BurstSignal`, `SnipeSlot`, `register_snipe_slot`, `unregister_snipe_slot`, `_global_snipe_burst`, `_run_burst_clear_loop`, `_register_spike_callback`, `spike_detector` parameter, `price_feeds` parameter, `sniper` parameter.

**Keep:** Window discovery via `GammaClient`, `CapitalManager` integration, session lifecycle, `MAX_CONCURRENT_SESSIONS`, `WINDOW_START_DELAY_S`.

**New `WindowManager.__init__` signature:**
```python
def __init__(
    self,
    bot: Any,
    config: Any,
    db: Any = None,
    risk_manager: Any = None,
    stats_tracker: Any = None,
    dry_run: bool = False,
    paper_trader: Any = None,
):
```

**New `_start_session`** — create `MakerLoop` with the new simplified signature (no sniper/spike_detector/burst_signal args).

#### `src/merge_engine.py` (MODIFY — apply Bug Fixes #1)

Apply all changes from Bug Fix #1:
1. Add `_MAX_MERGEABLE_RE` regex constant
2. Add `_clamp_and_retry` method
3. Wire retry into `_execute_merge_via_clob` after catching overestimation error
4. Remove `SNIPER_INVENTORY_RESERVE` constant entirely
5. Change `mergeable` calculation to `min(up_shares, down_shares)` (no reserve deduction)
6. Remove `_merge_via_rest` method and all Web3/RPC fallback code
7. Remove `from requests import ...` import if only used by the fallback

#### `src/main.py` (MODIFY)

Replace the `SnipeMakerStrategy` import and instantiation with `SpreadFarmerStrategy`:

```python
# Old:
from strategies.snipe_maker import SnipeMakerStrategy
self.strategy = SnipeMakerStrategy(bot=self.bot, config=self.config, ...)

# New:
from strategies.spread_farmer import SpreadFarmerStrategy
self.strategy = SpreadFarmerStrategy(bot=self.bot, config=self.config, dry_run=self.dry_run)
```

Remove the `spike_threshold_pct` parameter from `GabagoolBot.initialize_components()`.

#### `src/capital_manager.py` (MODIFY — apply Bug Fix #4)

Add `STOP_LOSS_TOLERANCE_PCT = 0.15` constant and update the stop-loss check as described in Bug Fix #4.

### LEAVE UNCHANGED (do not touch these files)

| File | Reason |
|------|--------|
| `src/bot.py` (TradingBot) | Order execution is correct. |
| `src/config.py` | Config structure is fine. |
| `src/gamma_client.py` | Market discovery works. |
| `src/auto_redeem.py` | Used for claiming winning naked shares after settlement. Keep. |
| `src/terminal_ui.py` | Logging formatting. Keep. |
| `src/db.py` | Database persistence. Keep. |
| `src/stats_tracker.py` | Performance metrics. Keep. |
| `src/paper_trader.py` | Used for dry-run simulation. Keep. |
| All `tests/` files | Keep for reference, update only if tests import deleted modules. |

---

## Part 5 — Configuration Changes

### `config/production.yaml` — exact changes required

```yaml
gabagool:
  # CHANGE: was 1.0 — causes stop-loss on every losing cycle
  auto_compound_pct: 0.0

  # KEEP: fixed session capital
  session_capital_usd: 200.0

  # CHANGE: was 0.96 — lower to match gabagool22's observed combined avg of 0.965
  max_combined_cost: 0.97

  # REMOVE entirely (snipe parameters — no longer applicable):
  # snipe_shares, snipe_ask_buffer, snipe_max_price, snipe_cooldown_s
  # spike_threshold_pct
  # momentum_sensitivity, entry_edge_threshold
  # min_signal_time_s, max_signal_time_s, momentum_lookback_s

  # KEEP:
  target_assets:
    - BTC
    - ETH
  max_concurrent_arbitrages: 4    # 4 windows at a time (matching gabagool22)
  max_position_per_market: 250.0  # $250 per window ($200 / 4 windows + buffer)
  max_total_exposure: 1000.0
  holding_time_limit: 1800
  min_time_to_resolution: 60
  max_daily_drawdown_pct: 0.15
  min_wallet_balance: 10.0
```

### `config/default.yaml` — remove all snipe/signal sections

Remove the entire `# MOMENTUM MAKER SIGNAL PARAMETERS` section and the `# LATENCY SNIPE PARAMETERS` section. These parameters no longer exist.

---

## Part 6 — What NOT to Do (common your mistakes from prior sessions)

This section exists because the your has made these mistakes before. Treat each item as a hard constraint.

1. **Do not claim a bug is fixed without running the code.** The merge "Expected at most two positions" error was claimed fixed twice without running. Always test before claiming.

2. **Do not enforce symmetrical position sizing.** The ladder is the same on both sides; the fill counts will be unequal because markets move. That is correct and expected.

3. **Do not reduce the ladder to 3 rungs.** The rate-limiting fix is sequential posting with delays, not reducing rungs.

4. **Do not deploy to the VPS before dry_run validation passes.** The VPS runs against real money. Dry-run first.

5. **Do not set `SNIPER_INVENTORY_RESERVE` to any non-zero value.** There is no sniper. Holding back inventory is pure waste.

6. **Do not add a Binance WebSocket connection to the pure spread bot.** gabagool22 does not use one for his spread strategy. It is only needed for spike detection, which is removed.

7. **Do not modify `BotConfig` to add `rpc_url`.** The Web3/RPC fallback code is being deleted entirely. Adding `rpc_url` to `BotConfig` just legitimizes dead code.

8. **Do not use `asyncio.gather` to post ladder rungs.** Sequential posting with 80ms delays is the fix. Parallel posting causes 425 floods.

9. **Do not calculate Net PnL using a `naked_payout` assumption before settlement.** Naked shares are uncertain until on-chain resolution. The in-window PnL is `merged_usdc - total_invested`, which will be negative. That is correct. Real profit shows up in the capital manager's balance delta after settlement.

10. **Do not touch `src/auto_redeem.py`.** It handles redeeming winning naked shares after settlement and is completely separate from the live trading loop.

---

## Part 7 — Verification Protocol

Must complete all three phases in order. Do not proceed to the next phase if the current phase fails.

### Phase 1 — Offline unit test (no network, no money)

**Gate:** All checks must pass before proceeding to Phase 2.

```bash
# In the repo root with dry_run=true and no API keys needed:
python -c "
from src.maker_loop import MakerLoop, LADDER_STEPS, MAX_COMBINED_COST_GATE
from src.merge_engine import MergeEngine, SNIPER_INVENTORY_RESERVE

# Check 1: Sniper inventory reserve is gone
assert SNIPER_INVENTORY_RESERVE == 0.0, f'Reserve must be 0, got {SNIPER_INVENTORY_RESERVE}'

# Check 2: Ladder has 15 rungs
assert len(LADDER_STEPS) == 15, f'Expected 15 rungs, got {len(LADDER_STEPS)}'

# Check 3: Ladder spans correct range
assert LADDER_STEPS[0] == 0.10 and LADDER_STEPS[-1] == 0.80

# Check 4: Cost gate is 0.97
assert MAX_COMBINED_COST_GATE == 0.97, f'Gate must be 0.97, got {MAX_COMBINED_COST_GATE}'

print('All unit checks passed.')
"
```

```bash
# Check 5: No references to spike_detector, sniper, or price_feed remain in active code
grep -r 'spike_detector\|SpikeDetector\|from src.sniper\|from src.price_feed' \
  src/ strategies/ --include='*.py'
# Expected: zero output. Any hits are bugs to fix.

# Check 6: No references to BurstSignal or GlobalSniperEngine
grep -r 'BurstSignal\|GlobalSniper\|burst_signal\|snipe_slot' \
  src/ strategies/ --include='*.py'
# Expected: zero output.

# Check 7: merge_engine has no SNIPER_INVENTORY_RESERVE
grep 'SNIPER_INVENTORY_RESERVE' src/merge_engine.py
# Expected: zero output.
```

### Phase 2 — Dry-run validation (live markets, simulated money)

**Run:** `python -m src.main --dry-run --config config/production.yaml --log-level DEBUG`

**Let it run for 30 minutes (two full 15-minute window cycles). Then check:**

```bash
# Check A: No 425 errors
grep '425' logs/gabagool.log | wc -l
# Expected: 0. Even 1-2 is acceptable if isolated. >10 means sequential posting is broken.

# Check B: Merges are happening
grep 'GASLESS MERGE MINED\|MERGE ══' logs/gabagool.log | wc -l
# Expected: >= 3 merges in 30 minutes of paper trading.

# Check C: No "exceeds maximum mergeable" errors
grep 'exceeds the maximum mergeable' logs/gabagool.log | wc -l
# Expected: 0 (the clamp-and-retry fix should handle this). If > 0, the retry is broken.

# Check D: Bot is not entering SNIPING state
grep 'SNIPING\|COOLDOWN\|spike_detector\|burst_signal' logs/gabagool.log | wc -l
# Expected: 0.

# Check E: Fills on both UP and DOWN
grep '▲ UP\|▼ DOWN' logs/gabagool.log | head -40
# Expected: mix of both directions in roughly similar proportions.

# Check F: Capital manager survives across cycles
grep 'CAPITAL MANAGER\|Settlement confirmed\|STOP-LOSS' logs/gabagool.log
# Expected: "Cycle N complete" lines visible, NO "STOP-LOSS TRIGGERED" lines.
```

**Pass criteria for Phase 2:** All six checks pass. If any fail, fix the underlying issue and repeat Phase 2 from scratch.

### Phase 3 — Live deployment (real money)

Only proceed here after Phase 2 passes.

```bash
# Deploy
ssh root@VPS "cd /root/GAANG-main && git pull && pip install -r requirements.txt"
ssh root@VPS "systemctl restart gabagool"  # or however you restart it

# Monitor for first 30 minutes
ssh root@VPS "tail -f /root/GAANG-main/logs/$(ls -t /root/GAANG-main/logs/ | grep bot-live | head -n 1)"
```

**Live pass criteria (first 30 minutes):**

| Metric | Pass | Fail → Action |
|--------|------|---------------|
| 425 errors | < 5 total | > 10 → roll back, re-check sequential posting |
| Merges confirmed on-chain | ≥ 1 tx hash in logs | 0 → check merge_engine, check API auth |
| "exceeds maximum mergeable" with NO retry | 0 | Any → merge retry is broken, fix immediately |
| Stop-loss trigger | 0 | Any → capital manager still aggressive, check tolerance % |
| Wallet balance after 2 cycles | Flat or positive | > $10 down → stop and investigate |

**Rollback command if anything fails:**
```bash
ssh root@VPS "cd /root/GAANG-main && git stash && systemctl restart gabagool"
```

---

## Summary: What the your Must Build

1. **Delete** 5 files: `spike_detector.py`, `sniper.py`, `price_feed.py`, `websocket_client.py`, `strategies/snipe_maker.py`

2. **Create** 1 file: `strategies/spread_farmer.py` (simple wiring, no spike/snipe deps)

3. **Rewrite** 2 files: `src/maker_loop.py` (remove snipe states), `src/window_manager.py` (remove GlobalSniperEngine)

4. **Modify** 4 files: `src/merge_engine.py` (clamp-retry + remove reserve), `src/capital_manager.py` (% stop-loss), `src/main.py` (use SpreadFarmerStrategy), `config/production.yaml` (auto_compound_pct=0.0)

5. **Run Phase 1 → Phase 2 → Phase 3 in order. Do not skip phases.**
