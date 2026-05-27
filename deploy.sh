#!/bin/bash
# ============================================================================
# Gabagool Bot — Deploy to Vultr VPS
# ============================================================================
# Usage: bash deploy.sh
#
# What this does:
#   1. Creates a clean tarball of only the essential files (no .venv, no data)
#   2. SCPs it to the VPS
#   3. SSHs in to unpack, create venv, install deps, and configure
# ============================================================================

set -e

VPS_HOST="root@149.248.59.42"
VPS_DIR="/root/gabagool-main"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
TARBALL="/tmp/gabagool-deploy.tar.gz"

echo "📦 Packaging essential files..."

cd "$LOCAL_DIR"

# Create tarball with ONLY what's needed to run
tar czf "$TARBALL" \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='data/*.db' \
  --exclude='data/*.json' \
  --exclude='*.html' \
  --exclude='.gemini' \
  --exclude='.antigravityignore' \
  --exclude='deploy.sh' \
  requirements.txt \
  Makefile \
  config/.env \
  config/.env.example \
  config/default.yaml \
  config/production.yaml \
  config/polymarket_cryptos.json \
  src/__init__.py \
  src/main.py \
  src/bot.py \
  src/client.py \
  src/config.py \
  src/crypto.py \
  src/db.py \
  src/gamma_client.py \
  src/http.py \
  src/maker_loop.py \
  src/paper_trader.py \
  src/price_feed.py \
  src/risk_manager.py \
  src/signer.py \
  src/sniper.py \
  src/spike_detector.py \
  src/stats_tracker.py \
  src/window_manager.py \
  src/position_tracker.py \
  src/auto_redeem.py \
  src/websocket_client.py \
  src/poly_merger.py \
  src/capital_manager.py \
  src/merge_engine.py \
  strategies/__init__.py \
  strategies/snipe_maker.py

echo "📤 Uploading to $VPS_HOST:$VPS_DIR..."
scp "$TARBALL" "$VPS_HOST:/tmp/gabagool-deploy.tar.gz"

echo "🔧 Setting up on VPS..."
ssh "$VPS_HOST" bash -s << 'REMOTE_SCRIPT'
set -e

# Unpack
mkdir -p /root/gabagool-main
cd /root/gabagool-main
tar xzf /tmp/gabagool-deploy.tar.gz
rm /tmp/gabagool-deploy.tar.gz

# Create data directory
mkdir -p data

# Install Python 3 venv if not present
if ! command -v python3 &>/dev/null; then
    echo "📥 Installing Python 3..."
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip
else
    # Just ensure venv is installed
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip
fi

# Create venv
echo "🐍 Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# Install deps
echo "📦 Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo ""
echo "✅ Gabagool Bot deployed to /root/gabagool-main"
echo ""
echo "Next steps:"
echo "  1. Edit config/.env with your keys:"
echo "     nano /root/gabagool-main/config/.env"
echo ""
echo "  2. Verify keys are set:"
echo "     source .venv/bin/activate"
echo "     cat config/.env"
echo ""
echo "  3. Dry run test:"
echo "     cd /root/gabagool-main && make run"
echo ""
echo "  4. Go live:"
echo "     cd /root/gabagool-main && make run-live"
echo ""
REMOTE_SCRIPT

# Cleanup local tarball
rm -f "$TARBALL"

echo ""
echo "🚀 Deploy complete!"
echo ""
echo "SSH in with: ssh $VPS_HOST"
echo "Then:        cd $VPS_DIR && make run"
