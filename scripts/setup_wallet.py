#!/usr/bin/env python3
"""
Gabagool Bot - Wallet Setup Script

Purpose:
    Setup and verify wallet configuration for Polymarket trading.
    Can generate new wallet or verify existing configuration.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Usage:
    # Verify existing wallet
    python scripts/setup_wallet.py

    # Generate new wallet
    python scripts/setup_wallet.py --generate

    # Approve Polymarket contracts
    python scripts/setup_wallet.py --approve

Notes:
    - NEVER share or commit private keys
    - Use dedicated wallet for bot trading
    - Keep backup of private key in secure location
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def generate_wallet():
    """Generate a new Polygon wallet."""
    print("Generating new wallet...")

    try:
        from eth_account import Account

        # Generate new account
        account = Account.create()

        print("\n" + "=" * 60)
        print("NEW WALLET GENERATED")
        print("=" * 60)
        print(f"Address: {account.address}")
        print(f"Private Key: {account.key.hex()}")
        print("=" * 60)
        print("\nIMPORTANT:")
        print("1. Save the private key in a secure location")
        print("2. NEVER share or commit the private key")
        print("3. Add to .env as: POLY_PRIVATE_KEY={account.key.hex()[2:]}")
        print("4. Fund with MATIC for gas and USDC for trading")
        print("=" * 60)

    except ImportError:
        print("ERROR: eth-account not installed")
        print("Run: pip install eth-account")


def verify_wallet():
    """Verify wallet configuration from .env."""
    print("Verifying wallet configuration...")

    # Load .env
    env_path = Path(__file__).parent.parent / "config" / ".env"
    if not env_path.exists():
        print(f"ERROR: .env file not found at {env_path}")
        print("Copy .env.example to .env and configure")
        return False

    # Read .env
    env_vars = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip()

    # Check required vars
    required = ["POLY_PRIVATE_KEY", "POLY_SAFE_ADDRESS"]
    missing = [k for k in required if not env_vars.get(k)]

    if missing:
        print(f"ERROR: Missing required variables: {missing}")
        return False

    private_key = env_vars.get("POLY_PRIVATE_KEY", "")
    safe_address = env_vars.get("POLY_SAFE_ADDRESS", "")

    # Validate private key format
    if private_key.startswith("0x"):
        print("WARNING: Private key should not have 0x prefix in .env")
        private_key = private_key[2:]

    if len(private_key) != 64:
        print(f"ERROR: Private key wrong length ({len(private_key)}, expected 64)")
        return False

    # Validate safe address format
    if not safe_address.startswith("0x") or len(safe_address) != 42:
        print(f"ERROR: Invalid safe address format: {safe_address}")
        return False

    print("\n" + "=" * 60)
    print("WALLET CONFIGURATION VERIFIED")
    print("=" * 60)
    print(f"Safe Address: {safe_address}")
    print(f"Private Key: {private_key[:8]}...{private_key[-8:]} (hidden)")
    print("=" * 60)

    return True


def approve_contracts():
    """Approve Polymarket contracts for token spending."""
    print("Approving Polymarket contracts...")
    print("PLACEHOLDER: Contract approval not yet implemented")
    print("This would approve USDC spending for Polymarket CLOB")


def main():
    parser = argparse.ArgumentParser(description="Wallet setup utility")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate new wallet"
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve Polymarket contracts"
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("GABAGOOL BOT - WALLET SETUP")
    print("=" * 60 + "\n")

    if args.generate:
        generate_wallet()
    elif args.approve:
        if verify_wallet():
            approve_contracts()
    else:
        verify_wallet()


if __name__ == "__main__":
    main()
