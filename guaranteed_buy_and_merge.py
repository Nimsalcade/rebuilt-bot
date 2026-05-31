import time
import sys
import logging
from src.config import Config
from src.bot import create_bot_from_config
from src.merge_engine import MergeEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("buy_and_merge")

def main():
    config = Config.load_with_env("config/production.yaml")
    bot = create_bot_from_config(config)
    engine = MergeEngine(bot, dry_run=False)

    condition_id = "0xf398b0e5016eeaee9b0885ed84012b6dc91269ac10d3b59d60722859c2e30b2f"
    question = "Will Harvey Weinstein be sentenced to no prison time?"
    tokens = [
        {"token_id": "24327803960645909378149041810697343640752122608192367041827900158592826352552"},
        {"token_id": "86488478623677188352872801318507143761188967461168408688159600382919967378486"}
    ]
    
    log.info("==================================================")
    log.info(f"Target Market: {question}")
    log.info(f"Condition ID: {condition_id}")
    log.info("==================================================")
    
    # We will buy 1.0 share of both sides at max price (0.99)
    # Using FOK (Fill Or Kill) to ensure we don't leave lingering open orders
    from py_clob_client.clob_types import OrderArgs, OrderType
    
    log.info("Placing aggressive BUY orders for 1.0 shares of BOTH tokens (price=0.99)...")
    
    try:
        r1 = bot.place_order(tokens[0]["token_id"], price=0.99, size=1.0, side="BUY", order_type="FOK")
        r2 = bot.place_order(tokens[1]["token_id"], price=0.99, size=1.0, side="BUY", order_type="FOK")
    except Exception as e:
        log.error(f"Failed to submit orders: {e}")
        return 1

    # bot.place_order returns a dictionary containing orderID. But it doesn't return size_matched.
    # We must fetch the order by ID to see size_matched.
    size1 = 0
    size2 = 0
    if r1 and r1.get("orderID"):
        o1 = bot.clob.get_order(r1["orderID"])
        size1 = float(o1.get("size_matched", 0)) if isinstance(o1, dict) else 0
    if r2 and r2.get("orderID"):
        o2 = bot.clob.get_order(r2["orderID"])
        size2 = float(o2.get("size_matched", 0)) if isinstance(o2, dict) else 0
    
    log.info(f"Leg 1 (YES) Matched: {size1} shares")
    log.info(f"Leg 2 (NO) Matched: {size2} shares")
    
    if size1 < 1.0 or size2 < 1.0:
        log.error("Failed to acquire at least 1.0 shares of BOTH tokens. Aborting merge.")
        return 1
        
    log.info("Waiting 6 seconds for the Graph/Subgraph to settle the balances...")
    time.sleep(6)
    
    log.info("Executing Gasless Merge for 1.0 shares...")
    result = engine._execute_merge_unified_sdk(condition_id, 1.0)
    
    if result.success:
        log.info(f"✅ MERGE MINED — tx_hash={result.tx_hash} | usdc_returned={result.usdc_returned}")
        print(f"\nSUCCESS: {result.tx_hash}")
        return 0
    else:
        log.error(f"❌ MERGE FAILED — {result.error}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
