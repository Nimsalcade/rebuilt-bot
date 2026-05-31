#!/usr/bin/env python3
"""
Live End-to-End Gasless Merge Test

1. Finds an active Polymarket market (has orderbook, not closed)
2. Buys 5 shares of UP at the lowest ask (using a high GTC limit)
3. Buys 5 shares of DOWN at the lowest ask
4. Initiates a gasless merge for the 5 pairs

Usage:
    python3 run_live_merge_test.py
"""

import sys
import time
import logging
import requests

from src.config import Config
from src.bot import create_bot_from_config
from src.merge_engine import MergeEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("live_merge_test")

def get_active_market():
    # Fetch top active markets by volume
    url = "https://gamma-api.polymarket.com/events?closed=false&limit=20&active=true"
    res = requests.get(url).json()
    
    for event in res:
        # We need a standard 2-outcome market that is active and has an orderbook
        if event.get("enableOrderBook") and not event.get("closed") and event.get("active"):
            try:
                tokens = eval(event.get("clobTokenIds", "[]"))
                if len(tokens) == 2:
                    return event, tokens
            except Exception:
                continue
    return None, None

def main() -> int:
    log.info("Loading config and initializing bot...")
    config = Config.load_with_env("config/production.yaml")
    bot = create_bot_from_config(config)
    engine = MergeEngine(bot, dry_run=False)

    log.info("Searching for an active 2-outcome market...")
    market, tokens = get_active_market()
    if not market:
        log.error("Could not find an active 2-outcome market")
        return 1
        
    condition_id = market["conditionId"]
    up_token = tokens[0]
    down_token = tokens[1]
    
    log.info(f"Target Market: {market.get('question', 'Unknown')}")
    log.info(f"Condition ID: {condition_id}")
    log.info(f"UP Token: {up_token[:10]}... | DOWN Token: {down_token[:10]}...")

    # We use a 0.99 GTC order, which crosses the book and acts as a market order
    size = 5.0
    buy_price = 0.99
    
    log.info(f"\n[1/3] BUYING {size} UP SHARES...")
    up_res = bot.place_order(up_token, price=buy_price, size=size, side="BUY", order_type="FOK")
    if not up_res:
        log.error("Failed to buy UP shares.")
        return 1
    log.info(f"UP Order placed: {up_res.get('orderID')}")

    log.info(f"\n[2/3] BUYING {size} DOWN SHARES...")
    down_res = bot.place_order(down_token, price=buy_price, size=size, side="BUY", order_type="FOK")
    if not down_res:
        log.error("Failed to buy DOWN shares.")
        return 1
    log.info(f"DOWN Order placed: {down_res.get('orderID')}")

    # Give Polymarket a moment to settle the fills
    log.info("\nWaiting 4 seconds for fills to settle on the subgraph...")
    time.sleep(4)

    log.info(f"\n[3/3] MERGING THE PAIR GASLESSLY...")
    result = engine._execute_merge_unified_sdk(condition_id, size)

    if result.success:
        log.info(f"✅ MERGE MINED — tx_hash={result.tx_hash} | merged={result.merged_shares} | usdc={result.usdc_returned}")
        print(f"\nSUCCESS! Transaction Hash: {result.tx_hash}")
        return 0
    else:
        log.error(f"❌ MERGE FAILED — {result.error}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
