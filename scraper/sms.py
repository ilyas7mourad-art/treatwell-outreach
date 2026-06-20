"""
SMS send entry point.

Usage:
    python -m scraper.sms --input output/leads_master_enriched.csv
    python -m scraper.sms --input output/leads_master_enriched.csv --dry-run
    python -m scraper.sms --input output/leads_master_enriched.csv --max-daily 10
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .sms_sender import send_sms, MAX_DAILY
from .utils import setup_logging


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Send SMS to leads with phone but no email (Brevo SMS API)."
    )
    p.add_argument("--input", required=True, help="Enriched leads CSV path")
    p.add_argument(
        "--max-daily",
        type=int,
        default=int(os.getenv("MAX_DAILY_EMAILS", MAX_DAILY)),
        help=f"Combined email+SMS daily cap (default: {MAX_DAILY})",
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be sent without sending")
    p.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(level=args.log_level)

    if not Path(args.input).exists():
        print(f"File not found: {args.input}")
        sys.exit(1)

    send_sms(
        csv_path=args.input,
        max_daily=args.max_daily,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
