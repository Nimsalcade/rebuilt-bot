import sys
import time
import logging
from src.config import Config
from src.bot import TradingBot, create_bot_from_config
from src.merge_engine import MergeEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ForceMergeTest")

def run_test():
    logger.info("Initializing Force Merge Test...")
    config = Config.load_with_env('config/production.yaml')
    
    # Ensure config has the relayer keys (loaded from .env)
    bot = create_bot_from_config(config)
    if not bot.connect():
        logger.error("Failed to connect to Polymarket")
        return
        
    merge_engine = MergeEngine(bot, config)
    
    condition_id = "0x384e2707bbb95da4bfa6f330fe7d5ccbec1c0a85e20be900cbf599987588e1a4"
    logger.info(f"Condition ID: {condition_id}")
    
    # We need to buy 15 shares of Yes and 15 shares of No.
    # To ensure immediate fills, we'll place aggressive limit orders (price 0.99)
    # The matching engine will fill them at the best available ask.
    buy_amount = 15.0
    aggressive_price = 0.99
    
    logger.info("Skipping buying, using existing shares...")
    # yes_order = bot.place_order(
    #     token_id=token_yes,
    #     price=aggressive_price,
    #     side="BUY",
    #     size=buy_amount
    # )
    # if not yes_order:
    #     logger.error("Failed to buy YES tokens")
    #     return
        
    # logger.info(f"Placing aggressive limit buy for {buy_amount} NO tokens...")
    # no_order = bot.place_order(
    #     token_id=token_no,
    #     price=aggressive_price,
    #     side="BUY",
    #     size=buy_amount
    # )
    # if not no_order:
    #     logger.error("Failed to buy NO tokens")
    #     return
        
    # logger.info("Waiting 3 seconds for fills to settle...")
    # time.sleep(3)
    
    logger.info(f"Executing forced gasless merge for Condition ID: {condition_id}...")
    result = merge_engine._execute_merge_unified_sdk(condition_id, "max")
    
    if result.success:
        logger.info(f"✅ SUCCESS! The gasless merge was executed. Tx Hash: {result.tx_hash}")
    else:
        logger.error(f"❌ FAILED! The gasless merge failed. Error: {result.error}")

if __name__ == "__main__":
    run_test()
