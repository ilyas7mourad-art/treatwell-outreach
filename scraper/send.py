#!/usr/bin/env python3
"""
Email send entry point.

Usage:
    python -m scraper.send --input output/leads_enriched.csv
    python -m scraper.send --input output/leads_enriched.csv --dry-run
    python -m scraper.send --input output/leads_enriched.csv --max-daily 5
"""

import argparse
import glob
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .email_sender import send_emails, MAX_DAILY, SEND_DELAY_MIN, SEND_DELAY_MAX
from .utils import setup_logging


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Send cold outreach emails from an enriched leads CSV."
    )
    p.add_argument(
        "--input",
        required=True,
        help="Enriched leads CSV (supports glob, e.g. output/leads_*_enriched.csv)",
    )
    p.add_argument(
        "--max-daily",
        type=int,
        default=int(os.getenv("MAX_DAILY_EMAILS", MAX_DAILY)),
        help=f"Max emails to send today across all runs (default: {MAX_DAILY})",
    )
    p.add_argument(
        "--delay-min",
        type=float,
        default=float(os.getenv("SEND_DELAY_MIN", SEND_DELAY_MIN)),
        help=f"Min seconds between sends (default: {SEND_DELAY_MIN})",
    )
    p.add_argument(
        "--delay-max",
        type=float,
        default=float(os.getenv("SEND_DELAY_MAX", SEND_DELAY_MAX)),
        help=f"Max seconds between sends (default: {SEND_DELAY_MAX})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be sent without actually sending",
    )
    p.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    logger = setup_logging(level=args.log_level)

    # Resolve glob pattern to a single file
    matches = sorted(glob.glob(args.input))
    if not matches:
        logger.error(f"No files matched: {args.input}")
        sys.exit(1)
    if len(matches) > 1:
        logger.warning(
            f"Multiple files matched glob — using the most recent: {matches[-1]}"
        )
    csv_path = matches[-1]

    if not Path(csv_path).exists():
        logger.error(f"File not found: {csv_path}")
        sys.exit(1)

    logger.info(f"=== Email Sender Starting ===")
    logger.info(f"Input: {csv_path}")
    logger.info(f"Daily limit: {args.max_daily} | Delay: {args.delay_min:.0f}-{args.delay_max:.0f}s")
    if args.dry_run:
        logger.info("[DRY RUN MODE — no emails will be sent]")

    send_emails(
        csv_path=csv_path,
        max_daily=args.max_daily,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
