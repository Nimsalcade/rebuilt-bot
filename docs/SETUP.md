# Gabagool Bot - Setup Guide

## Prerequisites

- Python 3.9+
- Polygon wallet with MATIC for gas
- USDC on Polygon for trading
- Polymarket account with proxy wallet

## Installation

### 1. Clone Repository

```bash
cd /home/botuser/bots
# gabagool directory should already exist
cd gabagool
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
# Copy example config
cp config/.env.example config/.env

# Edit with your credentials
nano config/.env
```

Required settings in `.env`:
```
POLY_PRIVATE_KEY=your_private_key_without_0x
POLY_SAFE_ADDRESS=0xYourPolymarketProxyAddress
```

### 5. Create Dedicated Wallet

**IMPORTANT**: Create a NEW wallet dedicated to this bot.

```bash
# Generate new wallet (or use existing dedicated one)
python scripts/setup_wallet.py
```

### 6. Fund Wallet

1. **MATIC** (~$5-10 for gas)
   - Send MATIC to your wallet on Polygon network

2. **USDC** (starting capital)
   - Send USDC to your wallet on Polygon
   - Start with $100 for testing

### 7. Approve Polymarket Contracts

The bot needs spending approval for Polymarket contracts.
This happens automatically on first trade, or run:

```bash
python scripts/setup_wallet.py --approve
```

## Verification

### Test API Connection

```bash
python tests/live/test_api_connection.py
```

### Test Wallet

```bash
python tests/live/test_wallet_balance.py
```

### Dry Run

```bash
python -m src.main --dry-run
```

## Production Deployment

### VPS Setup (Recommended)

1. Use Netherlands VPS for low latency
2. Install Python 3.9+
3. Clone repository
4. Configure .env with production wallet
5. Run in screen/tmux session

```bash
# SSH to VPS
ssh user@your-vps-ip

# Setup
cd /opt
git clone your-repo gabagool
cd gabagool
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp config/.env.example config/.env
nano config/.env

# Run in screen
screen -S gabagool
python -m src.main
# Ctrl+A, D to detach
```

### Monitor

```bash
# View logs
tail -f logs/gabagool.log

# Reattach to screen
screen -r gabagool

# Check database
sqlite3 gabagool.db "SELECT * FROM positions WHERE resolved=0;"
```

## Troubleshooting

### API Connection Failed
- Check internet connectivity
- Verify VPS location (some regions blocked)
- Check Polymarket API status

### Wallet Connection Failed
- Verify private key format (no 0x prefix)
- Check safe address matches Polymarket account
- Ensure wallet has MATIC for gas

### Orders Not Filling
- Check price thresholds
- Verify market liquidity
- Review slippage settings

## Security

1. **Never share private keys**
2. **Use dedicated trading wallet**
3. **Keep .env out of git**
4. **Monitor wallet activity**
5. **Start with small capital**
