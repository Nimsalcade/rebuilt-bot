#!/usr/bin/env python3
"""
Isolated gasless-merge proof. Run this BEFORE any funded maker run.

It builds the RelayClient exactly as src/merge_engine.py does, validates the
builder credentials, and attempts to merge ONE existing pair from a market you
specify. If it prints a tx hash, the credentials are correct and the bot's
recycle loop will work. If it errors, you've learned the cred mapping is still
off for $0 — no full run wasted.

The gasless relayer authenticates via the BUILDER triple (Polymarket Settings →
Builders → Create New; key/secret/passphrase are shown ONLY at creation time):

    POLY_BUILDER_API_KEY=<key>
    POLY_BUILDER_API_SECRET=<secret>
    POLY_BUILDER_API_PASSPHRASE=<passphrase>

NOTE the POLY_ prefix — config.get_env() prepends it. The Relayer-API-keys tab
(RELAYER_API_KEY / RELAYER_API_KEY_ADDRESS) is a DIFFERENT, header-based auth
that this SDK path does not use.

Usage:
    python test_merge.py <condition_id> [amount]

  <condition_id>  the market's condition id (0x...) holding the stuck pair
  [amount]        pair shares to merge (default 1.0)
"""
import sys
import logging

from src.config import Config
from src.bot import create_bot_from_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("test_merge")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    condition_id = sys.argv[1]
    amount = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    config = Config.from_env()
    bot = create_bot_from_config(config)

    # 1. Validate the builder triple up front with an actionable message.
    missing = [
        name for name, val in (
            ("POLY_BUILDER_API_KEY", config.builder_api_key),
            ("POLY_BUILDER_API_SECRET", config.builder_api_secret),
            ("POLY_BUILDER_API_PASSPHRASE", config.builder_api_passphrase),
        )
        if not (val or "").strip()
    ]
    if missing:
        log.error("Missing builder credentials: %s", ", ".join(missing))
        log.error("Set them in .env from Polymarket Settings → Builders → Create New "
                  "(values shown only at creation time). Note the POLY_ prefix.")
        return 1
    log.info("Builder credentials present (key=%s..., secret set, passphrase set)",
             config.builder_api_key[:8])

    # 2. Construct the merge engine and force a single merge on the given market.
    #    We import here so any SDK import error surfaces clearly.
    from src.merge_engine import MergeEngine

    engine = MergeEngine(bot, dry_run=False)
    if engine._merge_disabled:
        log.error("Merge engine disabled at startup: %s", engine._merge_disabled_reason)
        return 1

    log.info("Attempting on-chain merge: condition_id=%s amount=%s", condition_id, amount)
    result = engine._execute_merge_unified_sdk(condition_id, amount)

    if result.success:
        log.info("✅ MERGE MINED — tx_hash=%s | merged=%s | usdc=%s",
                 result.tx_hash, result.merged_shares, result.usdc_returned)
        print(f"\nSUCCESS: {result.tx_hash}")
        return 0

    log.error("❌ MERGE FAILED — %s", result.error)
    if MergeEngine._is_auth_error(result.error):
        log.error("This is a CREDENTIAL error: re-create the Builders key and re-copy "
                  "all three values (key/secret/passphrase) into .env.")
    print(f"\nFAILED: {result.error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
