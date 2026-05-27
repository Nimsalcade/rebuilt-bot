#!/usr/bin/env python3
"""
Gabagool Discord Weekly Report

Purpose:
    Send a summary of the 15-minute market monitor status to Discord webhook.
    Designed to run via cron at 7am Pacific time.

Author: AI-Generated
Created: 2026-01-27
Modified: 2026-01-27

Usage:
    python scripts/discord_weekly_report.py

Cron (7am Pacific = 15:00 UTC during PST, 14:00 UTC during PDT):
    0 15 * * 0 cd /home/botuser/bots/gabagool && /home/botuser/bots/gabagool/.venv/bin/python scripts/discord_weekly_report.py
"""

import os
import sys
import json
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Discord webhook URL
WEBHOOK_URL = "https://discord.com/api/webhooks/1444394184852111621/jWmTkmmr7yXKQ65S1eF3WBm1ffnIbu0I-Vva8vnInnZ5mF-iVbRD98BxEErVjG5zjNjD"

# Paths
GABAGOOL_DIR = Path(__file__).parent.parent
LOG_DIR = GABAGOOL_DIR / "data" / "logs"
MONITOR_LOG = GABAGOOL_DIR / "logs" / "monitor_v4.log"


def get_tmux_status() -> dict:
    """Check if gabagool tmux session is running."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions"],
            capture_output=True,
            text=True,
            timeout=5
        )
        sessions = result.stdout.strip()
        gabagool_running = "gabagool" in sessions.lower()
        return {
            "running": gabagool_running,
            "sessions": sessions
        }
    except Exception as e:
        return {"running": False, "error": str(e)}


def get_latest_monitor_stats() -> dict:
    """Parse the latest monitor log for stats."""
    stats = {
        "scan_count": 0,
        "best_combined": "N/A",
        "opportunities": 0,
        "paper_pnl": "$0.00",
        "last_scan_time": "N/A"
    }

    if not MONITOR_LOG.exists():
        return stats

    try:
        # Get last 100 lines of log
        result = subprocess.run(
            ["tail", "-100", str(MONITOR_LOG)],
            capture_output=True,
            text=True,
            timeout=5
        )
        lines = result.stdout.strip().split("\n")

        for line in reversed(lines):
            # Parse scan line: #3706 Best:BTC=1.800 Scan:182ms Win:389s/511s | Open:0 PnL:$+0.00
            if "#" in line and "Best:" in line:
                parts = line.split()
                for part in parts:
                    if part.startswith("#"):
                        stats["scan_count"] = int(part[1:])
                    elif part.startswith("Best:"):
                        stats["best_combined"] = part.replace("Best:", "")
                    elif part.startswith("PnL:"):
                        stats["paper_pnl"] = part.replace("PnL:", "")
                    elif part.startswith("Open:"):
                        stats["open_positions"] = int(part.replace("Open:", ""))

                # Extract timestamp
                if "[" in line and "]" in line:
                    ts = line.split("]")[0].replace("[", "")
                    stats["last_scan_time"] = ts
                break

    except Exception as e:
        stats["error"] = str(e)

    return stats


def get_data_file_stats() -> dict:
    """Get stats about collected data files."""
    stats = {
        "today_records": 0,
        "today_file_size": "0 KB",
        "total_files": 0
    }

    try:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        today_file = LOG_DIR / f"market_snapshots_{today}.jsonl"

        if today_file.exists():
            # Count lines
            result = subprocess.run(
                ["wc", "-l", str(today_file)],
                capture_output=True,
                text=True,
                timeout=10
            )
            stats["today_records"] = int(result.stdout.strip().split()[0])

            # File size
            size_bytes = today_file.stat().st_size
            if size_bytes > 1024 * 1024:
                stats["today_file_size"] = f"{size_bytes / (1024*1024):.1f} MB"
            else:
                stats["today_file_size"] = f"{size_bytes / 1024:.1f} KB"

        # Count all snapshot files
        if LOG_DIR.exists():
            stats["total_files"] = len(list(LOG_DIR.glob("market_snapshots_*.jsonl")))

    except Exception as e:
        stats["error"] = str(e)

    return stats


def get_opportunity_stats() -> dict:
    """Get opportunity statistics from log files."""
    stats = {
        "today_opportunities": 0,
        "best_margin_today": "N/A",
        "best_combined_today": "N/A",
        "best_combined_coin": "N/A"
    }

    try:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")

        # Check opportunities file
        opp_file = LOG_DIR / f"opportunities_{today}.jsonl"
        if opp_file.exists():
            with open(opp_file) as f:
                opportunities = [json.loads(line) for line in f if line.strip()]

            stats["today_opportunities"] = len(opportunities)

            if opportunities:
                best = max(opportunities, key=lambda x: x.get("margin", 0))
                stats["best_margin_today"] = f"{best.get('margin', 0):.2%}"
                stats["best_coin"] = best.get("coin", "N/A")

        # Analyze JSONL for best combined (sample last 1000 records for speed)
        snapshot_file = LOG_DIR / f"market_snapshots_{today}.jsonl"
        if snapshot_file.exists():
            best_combined = 2.0
            best_coin = "N/A"

            # Read last 1000 lines for analysis
            result = subprocess.run(
                ["tail", "-1000", str(snapshot_file)],
                capture_output=True,
                text=True,
                timeout=10
            )

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    for m in data.get("markets", []):
                        combined = m.get("combined", {}).get("best_ask", 2.0)
                        if combined < best_combined:
                            best_combined = combined
                            best_coin = m.get("coin", "N/A")
                except:
                    pass

            if best_combined < 2.0:
                stats["best_combined_today"] = f"{best_combined:.3f}"
                stats["best_combined_coin"] = best_coin

    except Exception as e:
        stats["error"] = str(e)

    return stats


def build_discord_message() -> dict:
    """Build the Discord webhook message."""
    tmux = get_tmux_status()
    monitor = get_latest_monitor_stats()
    data = get_data_file_stats()
    opps = get_opportunity_stats()

    now = datetime.now(timezone.utc)

    # Status emoji
    status_emoji = "✅" if tmux["running"] else "❌"

    # Build embed
    embed = {
        "title": f"{status_emoji} Gabagool Monitor Weekly Report",
        "color": 0x00FF00 if tmux["running"] else 0xFF0000,
        "timestamp": now.isoformat(),
        "fields": [
            {
                "name": "📊 Monitor Status",
                "value": f"**Running:** {'Yes' if tmux['running'] else 'No'}\n"
                        f"**Scans:** {monitor['scan_count']:,}\n"
                        f"**Last Scan:** {monitor['last_scan_time']}",
                "inline": True
            },
            {
                "name": "💰 Current Prices",
                "value": f"**Best Combined:** {monitor['best_combined']}\n"
                        f"**Paper PnL:** {monitor['paper_pnl']}\n"
                        f"**Open Positions:** {monitor.get('open_positions', 0)}",
                "inline": True
            },
            {
                "name": "📁 Data Collected",
                "value": f"**Today's Records:** {data['today_records']:,}\n"
                        f"**File Size:** {data['today_file_size']}\n"
                        f"**Total Files:** {data['total_files']}",
                "inline": True
            },
            {
                "name": "🎯 Opportunities",
                "value": f"**Today:** {opps['today_opportunities']}\n"
                        f"**Best Margin:** {opps['best_margin_today']}\n"
                        f"**Threshold:** < 1.00 combined",
                "inline": True
            }
        ],
        "footer": {
            "text": "Gabagool 15-Min Market Monitor | Polymarket Arbitrage"
        }
    }

    return {
        "embeds": [embed]
    }


def send_to_discord(message: dict) -> bool:
    """Send message to Discord webhook."""
    try:
        response = requests.post(
            WEBHOOK_URL,
            json=message,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        print(f"Discord message sent successfully: {response.status_code}")
        return True
    except Exception as e:
        print(f"Failed to send Discord message: {e}")
        return False


def main():
    """Main entry point."""
    print(f"Generating Gabagool weekly report at {datetime.now(timezone.utc).isoformat()}")

    message = build_discord_message()

    # Print message for debugging
    print(json.dumps(message, indent=2))

    # Send to Discord
    success = send_to_discord(message)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
