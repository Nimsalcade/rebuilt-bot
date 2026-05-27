# Fixing the Polymarket API Zero Balance Issue ($0.00)

## Problem Description
When running a trading bot on Polymarket, the bot may report that the wallet has `$0.00` available balance or throw an `insufficient balance` error, even when you have verified that the wallet holds USDC/pUSD on the Polygon blockchain.

**Log Signature:**
```text
WARNING - Skipping order: insufficient balance (have $0.0000, order needs ~$1.1000 w/ fees).
```

## Root Cause
Polymarket recently updated their account infrastructure:
1. **Legacy Accounts** use "Proxy Wallets". 
2. **New Accounts** automatically use "Deposit Wallets".

Many older trading bots and SDK implementations default to querying the Polymarket CLOB (Central Limit Order Book) API using `signature_type: 1` (Proxy Wallet format). 

If a new account (Deposit Wallet) is queried using the legacy `signature_type: 1`, the Polymarket API will return a balance of `0`, because it is scanning for funds in a non-existent Proxy Wallet instead of the user's actual Deposit Wallet.

## The Solution
To fix this issue, you must configure the bot's CLOB API client to explicitly use `signature_type: 3` (also known as `POLY_1271`). This instructs the Polymarket API to correctly route the balance queries and order signatures through the new Deposit Wallet architecture.

### How to Fix
Locate the bot's configuration file (e.g., `config/production.yaml` or `config/default.yaml`) and ensure the `clob` configuration block explicitly defines the `signature_type` as `3`.

```yaml
clob:
  host: "https://clob.polymarket.com"
  chain_id: 137               # Polygon mainnet
  signature_type: 3           # Polymarket Deposit Wallet (POLY_1271)
```

If the bot configures the `ClobClient` directly in Python, ensure it is instantiated with `SignatureTypeV2.POLY_1271`:

```python
from py_clob_client_v2 import ClobClient, SignatureTypeV2

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=private_key,
    signature_type=SignatureTypeV2.POLY_1271, # <--- THE FIX
    funder=safe_address
)
```

Once `signature_type: 3` is active, the Polymarket API will immediately return the correct account balance and successfully accept order signatures from the Deposit Wallet.
