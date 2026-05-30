import os
import sys
import logging
from datetime import datetime

# Force color because 'make run' uses 'tee' which breaks isatty() detection
USE_COLOR = True

# ANSI Colors
RESET = "\033[0m" if USE_COLOR else ""
RED = "\033[91m" if USE_COLOR else ""
GREEN = "\033[92m" if USE_COLOR else ""
YELLOW = "\033[93m" if USE_COLOR else ""
BLUE = "\033[94m" if USE_COLOR else ""
MAGENTA = "\033[95m" if USE_COLOR else ""
CYAN = "\033[96m" if USE_COLOR else ""
WHITE = "\033[97m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
DIM = "\033[2m" if USE_COLOR else ""

def _time():
    return datetime.now().strftime("%H:%M:%S")

def fmt_fill(market_id, side, price, shares, cost):
    color = GREEN if side == "UP" else RED
    arrow = "▲" if side == "UP" else "▼"
    return f"[{_time()}] FILLS   {market_id[:7]}  {color}{arrow} {side:<4}{RESET}  ${price:.3f} × {shares:<4.1f}   ${cost:.2f}"

def fmt_status(market_id, elapsed_s, state, up_shares, up_avg, down_shares, down_avg, invested, lean):
    lean_str = "even"
    if lean == "UP":
        lean_str = f"{GREEN}▲{RESET}"
    elif lean == "DOWN":
        lean_str = f"{RED}▼{RESET}"
    return f"[{_time()}] STATUS  {market_id[:7]}  {elapsed_s:>3.0f}s  {state:<7} │ UP {up_shares:.1f}sh@{up_avg:.2f}  DOWN {down_shares:.1f}sh@{down_avg:.2f}  INVESTED ${invested:.2f}  lean={lean_str}"

def fmt_merge(market_id, pairs, returned_usdc):
    return f"[{_time()}] {MAGENTA}══════ MERGE ══ {market_id[:7]}  {pairs:.2f} pairs → ${returned_usdc:.2f} USDC ════════════════{RESET}"

def fmt_window_close(market_id, cost, merged, naked_shares, naked_side, net_pnl, lean, signal):
    color = GREEN if net_pnl > 0 else (RED if net_pnl < 0 else DIM)
    signal_str = f"signal={signal} → " if signal else ""
    correct = "UNKNOWN"
    if signal and lean:
         correct = "CORRECT" if signal == lean else "WRONG"
    
    sign = "+" if net_pnl > 0 else ""
    
    lines = [
        f"[{_time()}] ┌─ WINDOW CLOSING: {market_id[:7]} ─────────────────────────────────────┐",
        f"           │  Cost: ${cost:.2f}  Merged: ${merged:.2f}  Naked: {naked_shares:.1f} {naked_side} sh{'':<14}│",
        f"           │  Net PnL: {color}{sign}${net_pnl:.2f}{RESET}  (lean={lean}, {signal_str}{correct}){'':<12}│",
        f"           └───────────────────────────────────────────────────────────────┘"
    ]
    return "\n".join(lines)

def fmt_gate(market_id, up_avg, down_avg, combined, limit):
    return f"[{_time()}] {YELLOW}⛔ GATE   {market_id[:7]}  UP ${up_avg:.3f} + DOWN ${down_avg:.3f} = ${combined:.3f} > ${limit:.3f}  LOCKED{RESET}"

def fmt_spike(direction, momentum, price, markets_count):
    color = GREEN if direction == "UP" else RED
    return f"[{_time()}] ⚡ SPIKE  BTC {color}{momentum:+.3f}%{RESET}  ${price:.0f}  → BURST → {markets_count} markets"

def fmt_snipe(market_id, success, price_paid, shares, error_msg):
    if success:
        return f"           SNIPE  {market_id[:7]}  {GREEN}FILLED{RESET} {shares:.1f} @ ${price_paid:.3f}"
    else:
        return f"           SNIPE  {market_id[:7]}  {RED}FAILED{RESET}  ({error_msg})"

def fmt_session_summary(start_time, end_time, total_windows, wins, losses, win_rate, invested, merged, redeemed, net_pnl, roi, balance, previous_balance):
    color = GREEN if net_pnl > 0 else (RED if net_pnl < 0 else DIM)
    arrow = "↑" if balance > previous_balance else ("↓" if balance < previous_balance else "-")
    sign = "+" if net_pnl > 0 else ""
    roi_sign = "+" if roi > 0 else ""
    
    lines = [
        f"",
        f"╔══════════════════════════════════════════════════════════════════╗",
        f"║  SESSION COMPLETE  │  {start_time} – {end_time}  │  {total_windows} windows{'':<15}║",
        f"╠══════════════════════════════════════════════════════════════════╣",
        f"║  Windows: {total_windows:<2}   Wins: {wins:<2}   Losses: {losses:<2}   Win Rate: {win_rate*100:.1f}%{'':<12}║",
        f"║  Invested: ${invested:<8.2f}   Merged: ${merged:<8.2f}   Redeemed: ${redeemed:<8.2f}         ║",
        f"║  NET PnL:  {color}{sign}${net_pnl:<8.2f}{RESET}   ROI: {color}{roi_sign}{roi*100:.1f}%{RESET}{'':<33}║",
        f"║  Balance:  ${balance:<8.2f}  ({arrow} from ${previous_balance:.2f}){'':<28}║",
        f"╚══════════════════════════════════════════════════════════════════╝",
        f""
    ]
    return "\n".join(lines)

def fmt_cycle_header(mode, btc_price=None, eth_price=None):
    btc_str = f"BTC ${btc_price:,.0f}" if btc_price is not None else "BTC N/A"
    eth_str = f"ETH ${eth_price:,.0f}" if eth_price is not None else "ETH N/A"
    lines = [
        f"",
        f"┌─────────────────────────────────────────────────────────────────────┐",
        f"│  GABAGOOL ENGINE  v1.0.0  │  {mode}  │  {btc_str}  {eth_str}   │",
        f"└─────────────────────────────────────────────────────────────────────┘",
    ]
    return "\n".join(lines)
