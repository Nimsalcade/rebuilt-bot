#!/usr/bin/env bash
#
# Script Name: deploy.sh
# Purpose: Deploy gabagool bot to VPS
# Author: AI-Generated
# Created: 2026-01-26
# Modified: 2026-01-26
#
# Usage:
#   ./scripts/deploy.sh [user@host]
#
# Dependencies:
#   - ssh access to VPS
#   - rsync
#
# Notes:
#   - Excludes .env, venv, __pycache__
#   - Creates backup on remote before deploy
#

set -euo pipefail

# Constants
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
readonly REMOTE_PATH="/opt/gabagool"

# Default remote host (override with argument)
REMOTE_HOST="${1:-user@your-vps-ip}"

echo "========================================"
echo "GABAGOOL BOT DEPLOYMENT"
echo "========================================"
echo "Project: $PROJECT_DIR"
echo "Remote: $REMOTE_HOST:$REMOTE_PATH"
echo "========================================"

# Confirm
read -p "Deploy to $REMOTE_HOST? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled."
    exit 0
fi

echo ""
echo "Step 1: Creating backup on remote..."
ssh "$REMOTE_HOST" "
    if [ -d $REMOTE_PATH ]; then
        BACKUP_NAME=${REMOTE_PATH}_backup_\$(date +%Y%m%d_%H%M%S)
        cp -r $REMOTE_PATH \$BACKUP_NAME
        echo \"Backup created: \$BACKUP_NAME\"
    else
        echo \"No existing deployment to backup\"
    fi
"

echo ""
echo "Step 2: Syncing files..."
rsync -avz --progress \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.git/' \
    --exclude 'config/.env' \
    --exclude '*.db' \
    --exclude 'logs/*.log' \
    --exclude 'samples/' \
    "$PROJECT_DIR/" \
    "$REMOTE_HOST:$REMOTE_PATH/"

echo ""
echo "Step 3: Setting up on remote..."
ssh "$REMOTE_HOST" "
    cd $REMOTE_PATH

    # Create venv if not exists
    if [ ! -d venv ]; then
        python3 -m venv venv
    fi

    # Install dependencies
    source venv/bin/activate
    pip install -r requirements.txt

    echo \"Setup complete\"
"

echo ""
echo "========================================"
echo "DEPLOYMENT COMPLETE"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. SSH to VPS: ssh $REMOTE_HOST"
echo "2. Configure .env: nano $REMOTE_PATH/config/.env"
echo "3. Start bot: cd $REMOTE_PATH && source venv/bin/activate && python -m src.main"
echo ""
