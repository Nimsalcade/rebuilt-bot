#!/usr/bin/env python3
"""
Accounting reconciliation test — the arithmetic proof required by §10.5 of the
project context before any live run.

It exercises the REAL accounting code (no mocks of the logic under test):

    - src.maker_loop.WindowFillSummary   (the four cost quantities)
    - src.capital_manager.CapitalManager (realized-PnL booking + naked registry)
    - src.auto_redeem.classify_naked_outcome (win/loss decision)
    - src.bot.TradingBot._refresh_balance / _record_balance_from_error (Bug C)

and proves, against an independent hand calculation, that:

    1. Realized PnL reconciles for a full buy -> merge -> resolution cycle with
       BOTH a winning naked leg and a losing one (Bugs A + B).
    2. At window close (merged portion only) the books never show a phantom dip.
    3. Skipping the losing leg overstates PnL by exactly its naked cost — i.e.
       the old winner-only path blinded the stop-loss (Bug B).
    4. Merge proceeds are not double-counted: a real balance refresh resets the
       optimistic accumulator, so the live balance stays bounded (Bug C).

Run standalone (no pytest required):

    python3 tests/test_accounting_reconciliation.py

Exits 0 on full reconciliation, 1 on any mismatch.
"""

import os
import sys
import types
import asyncio
import importlib


# ---------------------------------------------------------------------------
# Import the real modules in isolation.
#
# src/__init__.py eagerly imports the whole SDK stack (py_clob_client_v2, web3,
# ...), which isn't needed to test the accounting logic and may not be
# installed. We register a bare `src` package (so __init__.py never runs) and
# stub only the heavy *transitive* imports, never the code under test.
# ---------------------------------------------------------------------------
def _bootstrap_imports():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    src_dir = os.path.join(repo_root, "src")
    pkg = types.ModuleType("src")
    pkg.__path__ = [src_dir]
    sys.modules["src"] = pkg

    # auto_redeem references aiohttp.ClientSession / ClientTimeout at class-def
    # time (annotations). The function we test (classify_naked_outcome) never
    # touches the network, so a structural stub is sufficient.
    aio = types.ModuleType("aiohttp")
    aio.ClientSession = type("ClientSession", (), {})
    aio.ClientTimeout = lambda **kw: None
    sys.modules["aiohttp"] = aio

    mods = {
        "capital_manager": importlib.import_module("src.capital_manager"),
        "maker_loop": importlib.import_module("src.maker_loop"),
        "auto_redeem": importlib.import_module("src.auto_redeem"),
    }

    # Optional: the REAL TradingBot for the Bug C balance test. Stub the SDK
    # symbols bot.py imports at module load; we only ever call _refresh_balance
    # and _record_balance_from_error on a bare instance.
    try:
        for name in ("py_clob_client_v2", "py_clob_client_v2.exceptions",
                     "web3", "web3.middleware", "eth_account"):
            sys.modules.setdefault(name, types.ModuleType(name))
        pcc = sys.modules["py_clob_client_v2"]

        # Flexible stub: must be instantiable with arbitrary kwargs so calls
        # like BalanceAllowanceParams(asset_type=AssetType.COLLATERAL) succeed
        # and _refresh_balance reaches its real success path (where the reset
        # under test lives). A stub that rejected kwargs would silently divert
        # into the except branch and fake-pass the balance assertions.
        class _Stub:
            COLLATERAL = "COLLATERAL"
            def __init__(self, *a, **k):
                pass
        for sym in ("ClobClient", "ApiCreds", "OrderArgs", "OrderType",
                    "OpenOrderParams", "TradeParams", "PartialCreateOrderOptions",
                    "OrderPayload", "BalanceAllowanceParams", "AssetType"):
            setattr(pcc, sym, _Stub)
        sys.modules["py_clob_client_v2.exceptions"].PolyApiException = type(
            "PolyApiException", (Exception,), {})
        # bot.py does `from .client import GammaClient`
        client_stub = types.ModuleType("src.client")
        client_stub.GammaClient = type("GammaClient", (), {})
        sys.modules["src.client"] = client_stub
        mods["bot"] = importlib.import_module("src.bot")
    except Exception as exc:  # pragma: no cover - best effort
        print(f"  (note: real TradingBot import unavailable: {exc!r})")
        mods["bot"] = None

    return mods


MODS = _bootstrap_imports()
CapitalManager = MODS["capital_manager"].CapitalManager
WindowFillSummary = MODS["maker_loop"].WindowFillSummary
classify_naked_outcome = MODS["auto_redeem"].classify_naked_outcome


# ---------------------------------------------------------------------------
# Tiny assertion helpers (no pytest dependency).
# ---------------------------------------------------------------------------
EPS = 1e-9
_failures = []


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def check(label, got, want, tol=1e-6):
    ok = approx(got, want, tol)
    status = "PASS" if ok else "FAIL"
    print(f"    [{status}] {label}: got {got:.6f}, want {want:.6f}")
    if not ok:
        _failures.append(f"{label}: got {got}, want {want}")
    return ok


def check_bool(label, got, want):
    ok = (got == want)
    status = "PASS" if ok else "FAIL"
    print(f"    [{status}] {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(f"{label}: got {got!r}, want {want!r}")
    return ok


class FakeBot:
    """Minimal stand-in for the bot reference CapitalManager holds.

    Only carries the balance attributes CapitalManager.get_available_balance
    reads. dry_run is False so we exercise the live (cached + pending) path.
    """
    def __init__(self, cached_micro=None):
        self.config = types.SimpleNamespace(dry_run=False)
        self._cached_balance_micro = cached_micro
        self.pending_merge_proceeds = 0.0


# ---------------------------------------------------------------------------
# Scenario windows. Numbers chosen so the hand calc is easy to verify.
# ---------------------------------------------------------------------------
def make_window_1():
    """Winner naked leg: DOWN over-fills, DOWN wins at resolution."""
    return WindowFillSummary(
        market_id="win1",
        window_start=None,
        window_end=None,
        up_fills=1,   up_shares=0.0,   up_total_cost=48.00,  up_gross_shares=100.0,
        down_fills=1, down_shares=20.0, down_total_cost=58.80, down_gross_shares=120.0,
        merged_usdc=100.0,
    )


def make_window_2():
    """Loser naked leg: UP over-fills, DOWN wins so the UP leftover expires."""
    return WindowFillSummary(
        market_id="win2",
        window_start=None,
        window_end=None,
        up_fills=1,   up_shares=50.0,  up_total_cost=100.00, up_gross_shares=200.0,
        down_fills=1, down_shares=0.0, down_total_cost=69.00, down_gross_shares=150.0,
        merged_usdc=150.0,
    )


def test_summary_cost_decomposition():
    """The four quantities must satisfy merged + naked == total invested."""
    print("\n[1] WindowFillSummary cost decomposition (source of the 4 quantities)")
    for name, w in (("window1", make_window_1()), ("window2", make_window_2())):
        print(f"  {name}:")
        check(f"{name} total_invested == merged_cb + naked_cb",
              w.merged_usdc_cost_basis + w.naked_cost_basis, w.total_invested)

    w1 = make_window_1()
    check("w1 up_avg_cost", w1.up_avg_cost, 0.48)
    check("w1 down_avg_cost", w1.down_avg_cost, 0.49)
    check("w1 total_invested", w1.total_invested, 106.80)
    check("w1 merged_cost_basis", w1.merged_usdc_cost_basis, 97.00)
    check("w1 naked_cost_basis", w1.naked_cost_basis, 9.80)
    check("w1 naked_shares", w1.naked_shares, 20.0)
    check_bool("w1 lean_direction", w1.lean_direction, "DOWN")

    w2 = make_window_2()
    check("w2 total_invested", w2.total_invested, 169.00)
    check("w2 merged_cost_basis", w2.merged_usdc_cost_basis, 144.00)
    check("w2 naked_cost_basis", w2.naked_cost_basis, 25.00)
    check("w2 naked_shares", w2.naked_shares, 50.0)
    check_bool("w2 lean_direction", w2.lean_direction, "UP")


def test_classifier():
    """The pure win/loss decision the resolver feeds into booking."""
    print("\n[2] classify_naked_outcome (resolver decision)")
    check_bool("naked DOWN, DOWN wins -> won", classify_naked_outcome("DOWN", "DOWN"), True)
    check_bool("naked UP, DOWN wins -> lost", classify_naked_outcome("UP", "DOWN"), False)
    check_bool("naked UP, UP wins -> won", classify_naked_outcome("UP", "UP"), True)
    check_bool("undecided (no winner) -> None", classify_naked_outcome("UP", None), None)
    check_bool("no naked side -> None", classify_naked_outcome(None, "UP"), None)


def test_full_reconciliation():
    """Book both windows through the real CapitalManager and reconcile PnL."""
    print("\n[3] Full realized-PnL reconciliation (buy -> merge -> resolution)")
    cm = CapitalManager(bot=FakeBot(), session_capital_usd=1000.0)

    w1, w2 = make_window_1(), make_window_2()
    cid1, cid2 = "0xcond1", "0xcond2"

    # --- Phase A: both windows CLOSE (book MERGED portion only) -------------
    for w in (w1, w2):
        cm.record_window_resolution(
            merged_cost_basis=w.merged_usdc_cost_basis,
            merged_returns=w.merged_usdc,
        )
    cm.register_naked_position(cid1, w1.lean_direction, w1.naked_shares, w1.naked_cost_basis)
    cm.register_naked_position(cid2, w2.lean_direction, w2.naked_shares, w2.naked_cost_basis)

    pnl_at_close = (cm.total_merged_returns + cm.total_redeemed_returns) - cm.gross_spent_resolved
    print(f"    after close-only: merged_ret={cm.total_merged_returns:.2f} "
          f"redeemed={cm.total_redeemed_returns:.2f} gross={cm.gross_spent_resolved:.2f}")
    # merged: 100 + 150 = 250 ; gross (merged cb only): 97 + 144 = 241
    check("close-only gross (merged cb only)", cm.gross_spent_resolved, 241.00)
    check("close-only realized PnL (must be >= 0, no phantom dip)", pnl_at_close, 9.00)
    if pnl_at_close < -EPS:
        _failures.append("phantom loss at window close")

    # Registry lookup is the data flow Bug A needed (conditionId -> cost basis)
    check("registry lookup cid1 naked cost", cm.get_naked_cost_basis(cid1), 9.80)
    check("registry lookup cid2 naked cost", cm.get_naked_cost_basis(cid2), 25.00)

    # --- Phase B: naked legs RESOLVE on-chain ------------------------------
    # win1: naked DOWN, DOWN wins -> winner, payout = 20 shares * $1.
    won1 = classify_naked_outcome(w1.lean_direction, "DOWN")
    booked1 = cm.record_naked_resolution(cid1, won=won1, redeemed_payout=20.0)
    # win2: naked UP, DOWN wins -> loser, payout 0.
    won2 = classify_naked_outcome(w2.lean_direction, "DOWN")
    booked2 = cm.record_naked_resolution(cid2, won=won2, redeemed_payout=0.0)
    check_bool("win1 booked", booked1, True)
    check_bool("win2 booked", booked2, True)
    check_bool("win1 classified won", won1, True)
    check_bool("win2 classified lost", won2, False)

    # Idempotency: re-booking must be a no-op (no double counting).
    check_bool("re-book cid1 is no-op", cm.record_naked_resolution(cid1, won=True, redeemed_payout=20.0), False)
    check("registry lookup after resolution is 0", cm.get_naked_cost_basis(cid1), 0.0)

    # --- Reconcile against the independent hand calc -----------------------
    # Hand calc:
    #   merged returns   = 100 + 150                 = 250.00
    #   redeemed payouts = 20  + 0                   =  20.00
    #   gross spent      = 97 + 9.80 + 144 + 25      = 275.80  (== total invested!)
    #   realized PnL     = (250 + 20) - 275.80       =  -5.80
    realized = (cm.total_merged_returns + cm.total_redeemed_returns) - cm.gross_spent_resolved
    total_invested = w1.total_invested + w2.total_invested
    print(f"    final: merged_ret={cm.total_merged_returns:.2f} "
          f"redeemed={cm.total_redeemed_returns:.2f} gross={cm.gross_spent_resolved:.2f} "
          f"-> realized PnL={realized:.2f}")
    check("final total_merged_returns", cm.total_merged_returns, 250.00)
    check("final total_redeemed_returns", cm.total_redeemed_returns, 20.00)
    check("gross_spent_resolved == total invested", cm.gross_spent_resolved, total_invested)
    check("gross_spent_resolved value", cm.gross_spent_resolved, 275.80)
    check("realized PnL", realized, -5.80)

    # Independent reconciliation: PnL == (all returns) - (all costs)
    all_returns = 250.00 + 20.00
    check("realized PnL == returns - costs (hand calc)", realized, all_returns - total_invested)


def test_losing_leg_visibility():
    """Skipping the loser overstates PnL by exactly its naked cost (Bug B)."""
    print("\n[4] Losing-leg visibility (the old winner-only path was blind)")

    w1, w2 = make_window_1(), make_window_2()

    # Correct books (both legs resolved).
    cm = CapitalManager(bot=FakeBot(), session_capital_usd=1000.0)
    for w, cid, won, pay in ((w1, "a", True, 20.0), (w2, "b", False, 0.0)):
        cm.record_window_resolution(merged_cost_basis=w.merged_usdc_cost_basis,
                                    merged_returns=w.merged_usdc)
        cm.register_naked_position(cid, w.lean_direction, w.naked_shares, w.naked_cost_basis)
        cm.record_naked_resolution(cid, won=won, redeemed_payout=pay)
    correct = (cm.total_merged_returns + cm.total_redeemed_returns) - cm.gross_spent_resolved

    # Buggy books: winner booked, loser silently dropped.
    cmb = CapitalManager(bot=FakeBot(), session_capital_usd=1000.0)
    for w, cid, won, pay, book in ((w1, "a", True, 20.0, True), (w2, "b", False, 0.0, False)):
        cmb.record_window_resolution(merged_cost_basis=w.merged_usdc_cost_basis,
                                     merged_returns=w.merged_usdc)
        cmb.register_naked_position(cid, w.lean_direction, w.naked_shares, w.naked_cost_basis)
        if book:  # only winners ever get booked in the old path
            cmb.record_naked_resolution(cid, won=won, redeemed_payout=pay)
    buggy = (cmb.total_merged_returns + cmb.total_redeemed_returns) - cmb.gross_spent_resolved

    overstatement = buggy - correct
    print(f"    correct PnL={correct:.2f}  buggy(winner-only) PnL={buggy:.2f}  "
          f"overstatement={overstatement:.2f}")
    check("overstatement == dropped loser's naked cost (25.00)", overstatement, 25.00)
    check("correct realized PnL", correct, -5.80)
    check("buggy realized PnL (overstated)", buggy, 19.20)


def test_stop_loss_trips_on_real_loss():
    """A genuine realized loss past the floor must trip the stop-loss."""
    print("\n[5] Stop-loss fires on a real realized loss")
    shutdown = asyncio.Event()
    cm = CapitalManager(bot=FakeBot(), session_capital_usd=1000.0, shutdown_event=shutdown)
    # Book a clean -$200 realized loss: one window, naked leg loses big.
    cm.record_window_resolution(merged_cost_basis=0.0, merged_returns=0.0)
    cm.register_naked_position("loss", "UP", 200.0, 200.0)
    cm.record_naked_resolution("loss", won=False, redeemed_payout=0.0)
    realized = (cm.total_merged_returns + cm.total_redeemed_returns) - cm.gross_spent_resolved
    print(f"    realized PnL={realized:.2f}  floor=-150.00")
    ok = cm.check_stop_loss()  # returns False when tripped
    check_bool("check_stop_loss returns False (tripped)", ok, False)
    check_bool("shutdown_event set", shutdown.is_set(), True)


def test_merge_proceeds_no_double_count():
    """Bug C: a real refresh resets pending_merge_proceeds; no double count."""
    print("\n[6] Merge-proceeds double-count guard (Bug C)")
    bot_mod = MODS["bot"]
    if bot_mod is None:
        print("    (skipped: real TradingBot unavailable in this environment)")
        return

    TradingBot = bot_mod.TradingBot

    # Bare instance — skip __init__ (which builds the live CLOB client).
    bot = TradingBot.__new__(TradingBot)
    bot.logger = MODS["bot"].logging.getLogger("test_bot")
    bot.config = types.SimpleNamespace(dry_run=False)
    bot._cached_balance_micro = 400_000_000   # $400 on-chain
    bot._balance_cached_at = 0.0
    bot.pending_merge_proceeds = 0.0
    bot._last_insufficient_log_at = 0.0

    cm = CapitalManager(bot=bot, session_capital_usd=1000.0)

    async def available():
        return await cm.get_available_balance()

    # 1) Merge confirms $60 optimistically (as MergeEngine does). Not yet on chain.
    bot.pending_merge_proceeds += 60.0
    bal = asyncio.run(available())
    check("after optimistic merge credit", bal, 460.0)

    # 2) Chain settles; a real refresh reads the new $460 balance.
    settled_micro = 460_000_000
    bot.clob = types.SimpleNamespace(
        get_balance_allowance=lambda *a, **k: {"balance": str(settled_micro)}
    )
    bot._refresh_balance()  # REAL method — must reset pending to 0
    check("pending reset to 0 after refresh", bot.pending_merge_proceeds, 0.0)
    bal = asyncio.run(available())
    check("no double count after refresh (460, not 520)", bal, 460.0)

    # 3) Hammer it: 50 merge+settle cycles must not inflate without bound.
    on_chain = 460_000_000
    for _ in range(50):
        proceeds = 60.0
        bot.pending_merge_proceeds += proceeds          # optimistic credit
        on_chain += int(proceeds * 1_000_000)           # merge settles on chain
        bot.clob.get_balance_allowance = (lambda v: (lambda *a, **k: {"balance": str(v)}))(on_chain)
        bot._refresh_balance()                          # real refresh reconciles
    bal = asyncio.run(available())
    expected = on_chain / 1_000_000                     # 460 + 50*60 = 3460
    print(f"    after 50 merge/settle cycles: balance={bal:.2f} (expected {expected:.2f})")
    check("bounded balance after 50 cycles", bal, expected)
    check("pending still 0 after 50 cycles", bot.pending_merge_proceeds, 0.0)

    # 4) Same guard via the error-parse path (_record_balance_from_error).
    bot.pending_merge_proceeds += 75.0
    bot._record_balance_from_error("balance: 500000000, sum of matched orders: 0, sum of active orders: 0")
    check("pending reset by error-path refresh", bot.pending_merge_proceeds, 0.0)


class FakeGamma:
    """Stand-in for GammaClient.get_resolution (no network)."""
    def __init__(self, resolutions):
        self._r = resolutions

    def get_resolution(self, condition_id):
        return self._r.get(condition_id)


def test_redeemer_wiring_end_to_end():
    """Drive the REAL AutoRedeemer.check_and_redeem path that used to crash.

    Proves Bug A (no TypeError, real value booked) and Bug B (losing leg booked
    via the resolution sweep) end to end, landing on the same -5.80 hand calc.
    """
    print("\n[7] AutoRedeemer wiring end-to-end (Bug A no-crash + Bug B sweep)")
    AutoRedeemer = MODS["auto_redeem"].AutoRedeemer

    w1, w2 = make_window_1(), make_window_2()
    cid1, cid2 = "0xwin", "0xlose"

    cm = CapitalManager(bot=FakeBot(), session_capital_usd=1000.0)
    # Windows close: merged booked, naked registered (as window_manager does).
    for w, cid in ((w1, cid1), (w2, cid2)):
        cm.record_window_resolution(merged_cost_basis=w.merged_usdc_cost_basis,
                                    merged_returns=w.merged_usdc)
        cm.register_naked_position(cid, w.lean_direction, w.naked_shares, w.naked_cost_basis)

    # Resolutions: both markets settle DOWN. w1 naked=DOWN -> win; w2 naked=UP -> loss.
    gamma = FakeGamma({
        cid1: {"resolved": True, "winning_outcome": "DOWN"},
        cid2: {"resolved": True, "winning_outcome": "DOWN"},
    })
    redeemer = AutoRedeemer(
        wallet_address="0xtest",
        enabled=True,
        capital_manager=cm,
        gamma_client=gamma,
    )
    # Winner is the only "redeemable" position the Data API returns; its claimed
    # value is the real $20 payout. Losers never appear here (that's Bug B).
    async def fake_redeemable():
        return [{"id": "p1", "conditionId": cid1, "value": 20.0, "slug": "win-mkt"}]
    redeemer.get_redeemable_positions = fake_redeemable

    async def fake_redeem(condition_id):
        return True  # simulate a successful on-chain claim
    redeemer.redeem_position = fake_redeem

    processed = asyncio.run(redeemer.check_and_redeem())  # the old crash site
    check("redeemer processed 1 winner (no crash)", processed, 1.0)

    realized = (cm.total_merged_returns + cm.total_redeemed_returns) - cm.gross_spent_resolved
    print(f"    after live sweep: merged_ret={cm.total_merged_returns:.2f} "
          f"redeemed={cm.total_redeemed_returns:.2f} gross={cm.gross_spent_resolved:.2f} "
          f"-> realized PnL={realized:.2f}")
    check("winner payout booked ($20)", cm.total_redeemed_returns, 20.00)
    check("both naked legs booked into gross", cm.gross_spent_resolved, 275.80)
    check("live-path realized PnL matches hand calc", realized, -5.80)
    check("no naked positions left pending", float(len(cm.pending_naked_positions())), 0.0)


def main():
    print("=" * 72)
    print("ACCOUNTING RECONCILIATION — arithmetic proof (project context §10.5)")
    print("=" * 72)

    test_summary_cost_decomposition()
    test_classifier()
    test_full_reconciliation()
    test_losing_leg_visibility()
    test_stop_loss_trips_on_real_loss()
    test_merge_proceeds_no_double_count()
    test_redeemer_wiring_end_to_end()

    print("\n" + "=" * 72)
    if _failures:
        print(f"RESULT: FAIL — {len(_failures)} mismatch(es):")
        for f in _failures:
            print(f"  - {f}")
        print("=" * 72)
        return 1
    print("RESULT: PASS — all numbers reconcile against the hand calculation.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
