#!/usr/bin/env python3
"""
Merge Gabagool VPS bot log files into a single chronologically sorted CSV.

Parses standard Python logging lines, console FILLS/STATUS lines, and UI/raw lines.

Usage:
    python scripts/merge_vps_logs.py
    python scripts/merge_vps_logs.py -o vps_logs/merged.csv log1.log log2.log
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STANDARD_LOG = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - ([^-]+?) - (\w+) - (.*)$"
)
CONSOLE_LOG = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\] (\w+)\s+(.*)$")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
UI_LINE = re.compile(r"^[┌│└]|^â[“”]|GABAGOOL ENGINE")

DEFAULT_SINCE = "2026-05-28 20:00:00"
DEFAULT_SINCE_TZ = "America/New_York"

DEFAULT_LOGS = [
    "vps_logs/bot-live-2026-05-29-00-15.log",
    "vps_logs/bot-live-2026-05-29-01-44.log",
    "vps_logs/bot-live-2026-05-29-03-20.log",
    "vps_logs/bot-live-2026-05-29-03-22.log",
    "vps_logs/bot-live-2026-05-29-03-27.log",
    "vps_logs/bot-live-2026-05-29-09-23.log",
    "vps_logs/bot-live-2026-05-29-09-45.log",
    "vps_logs/bot-live-2026-05-29-09-47.log",
    "vps_logs/bot-live-2026-05-29-09-57.log",
    "vps_logs/bot-live-2026-05-29-10-22.log",
    "vps_logs/bot-live-2026-05-29-12-06.log",
    "vps_logs/bot-live-2026-05-29-12-21.log",
    "vps_logs/bot-live-2026-05-29-12-24.log",
    "vps_logs/bot-live-2026-05-29-12-26.log",
    "vps_logs/bot-live-2026-05-29-12-43.log",
    "vps_logs/bot-live-2026-05-29-14-17.log",
    "vps_logs/bot-live-2026-05-29-14-27.log",
    "vps_logs/bot-live-2026-05-29-16-35.log",
    "vps_logs/bot-live-2026-05-29-18-10.log",
    "vps_logs/bot-live-2026-05-29-18-12.log",
    "vps_logs/bot-live-2026-05-29-18-27.log",
    "vps_logs/bot-live-2026-05-29-18-39.log",
    "vps_logs/bot-live-2026-05-29-18-45.log",
    "vps_logs/bot-live-2026-05-29-19-10.log",
    "vps_logs/bot-live-2026-05-29-19-23.log",
    "vps_logs/latest_pull.log",
]

CSV_FIELDS = [
    "timestamp",
    "timestamp_sort",
    "logger",
    "level",
    "category",
    "message",
    "line_type",
    "source_file",
    "source_line",
]


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def parse_timestamp(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S,%f")
    except ValueError:
        return None


def date_from_filename(path: Path) -> str | None:
    match = re.search(r"bot-live-(\d{4}-\d{2}-\d{2})-", path.name)
    if match:
        return match.group(1)
    return None


def file_start_sort_key(path: Path) -> str:
    match = re.search(r"bot-live-(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})\.log", path.name)
    if match:
        date_part, hour, minute = match.groups()
        return f"{date_part}T{hour}:{minute}:00"
    date_part = date_from_filename(path)
    if date_part:
        return f"{date_part}T00:00:00"
    return "1970-01-01T00:00:00"


def parse_log_file(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_date = date_from_filename(path)
    file_start = file_start_sort_key(path)
    last_sort_key = file_start

    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\n\r")
            if not line.strip():
                continue

            cleaned = strip_ansi(line)
            row: dict[str, str] = {
                "timestamp": "",
                "timestamp_sort": "",
                "logger": "",
                "level": "",
                "category": "",
                "message": cleaned,
                "line_type": "raw",
                "source_file": path.name,
                "source_line": str(line_no),
            }

            standard = STANDARD_LOG.match(cleaned)
            if standard:
                ts_raw, logger, level, message = standard.groups()
                ts = parse_timestamp(ts_raw)
                current_date = ts_raw[:10] if ts else current_date
                row.update(
                    {
                        "timestamp": ts_raw,
                        "timestamp_sort": ts.isoformat() if ts else ts_raw,
                        "logger": logger.strip(),
                        "level": level,
                        "message": message,
                        "line_type": "standard",
                    }
                )
                last_sort_key = row["timestamp_sort"]
                rows.append(row)
                continue

            console = CONSOLE_LOG.match(cleaned)
            if console:
                time_part, category, message = console.groups()
                ts_raw = f"{current_date} {time_part},000" if current_date else time_part
                ts = parse_timestamp(ts_raw) if current_date else None
                row.update(
                    {
                        "timestamp": ts_raw,
                        "timestamp_sort": ts.isoformat() if ts else f"{current_date or '1970-01-01'}T{time_part}",
                        "category": category,
                        "message": message,
                        "line_type": "console",
                    }
                )
                last_sort_key = row["timestamp_sort"]
                rows.append(row)
                continue

            if UI_LINE.search(cleaned):
                row["line_type"] = "ui"
                row["timestamp_sort"] = last_sort_key
                rows.append(row)
                continue

            row["timestamp_sort"] = last_sort_key
            rows.append(row)

    return rows


def parse_sort_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def since_cutoff_utc(since: str, tz_name: str) -> datetime:
    local = datetime.strptime(since, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def filter_rows_since(rows: list[dict[str, str]], cutoff: datetime) -> list[dict[str, str]]:
    kept: list[dict[str, str]] = []
    for row in rows:
        ts = parse_sort_datetime(row["timestamp_sort"])
        if ts is None or ts >= cutoff:
            kept.append(row)
    return kept


def merge_logs(log_paths: list[Path], since_cutoff: datetime | None = None) -> list[dict[str, str]]:
    all_rows: list[dict[str, str]] = []
    for path in log_paths:
        if not path.exists():
            raise FileNotFoundError(f"Log file not found: {path}")
        all_rows.extend(parse_log_file(path))

    all_rows.sort(key=lambda row: (row["timestamp_sort"], row["source_file"], int(row["source_line"])))
    if since_cutoff is not None:
        all_rows = filter_rows_since(all_rows, since_cutoff)
    return all_rows


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Merge VPS bot logs into one CSV file.")
    parser.add_argument(
        "logs",
        nargs="*",
        help="Log files to merge (defaults to the 2026-05-29 bot-live set).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="vps_logs/bot-live-2026-05-29-merged.csv",
        help="Output CSV path (default: vps_logs/bot-live-2026-05-29-merged.csv)",
    )
    parser.add_argument(
        "--since",
        default=DEFAULT_SINCE,
        help=f"Include rows on/after this time (default: {DEFAULT_SINCE})",
    )
    parser.add_argument(
        "--since-tz",
        default=DEFAULT_SINCE_TZ,
        help=f"Timezone for --since (default: {DEFAULT_SINCE_TZ})",
    )
    parser.add_argument(
        "--no-since-filter",
        action="store_true",
        help="Include all rows regardless of --since",
    )
    args = parser.parse_args()

    log_paths = [repo_root / p for p in (args.logs or DEFAULT_LOGS)]
    output_path = repo_root / args.output

    since_cutoff = None
    if not args.no_since_filter:
        since_cutoff = since_cutoff_utc(args.since, args.since_tz)

    rows = merge_logs(log_paths, since_cutoff=since_cutoff)
    write_csv(rows, output_path)

    type_counts: dict[str, int] = {}
    for row in rows:
        type_counts[row["line_type"]] = type_counts.get(row["line_type"], 0) + 1

    print(f"Merged {len(log_paths)} log files -> {output_path}")
    if since_cutoff is not None:
        print(f"Filtered to rows >= {args.since} {args.since_tz} (UTC {since_cutoff:%Y-%m-%d %H:%M:%S})")
    print(f"Total rows: {len(rows)}")
    for line_type, count in sorted(type_counts.items()):
        print(f"  {line_type}: {count}")


if __name__ == "__main__":
    main()
