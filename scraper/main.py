#!/usr/bin/env python3
"""
Treatwell Barbershop Scraper — entry point.

Usage:
    python -m scraper.main --site uk
    python -m scraper.main --site uk fr de
    python -m scraper.main --site all
    python -m scraper.main --site uk --cities london manchester --max-pages 5
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .scraper import scrape_site, SITES
from .exporters import export_csv, export_google_sheets
from .utils import setup_logging


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape Treatwell barbershop listings and export leads."
    )
    all_site_keys = list(SITES.keys())
    p.add_argument(
        "--site",
        nargs="+",
        default=["uk"],
        help=f"Site(s) to scrape: {all_site_keys} or 'all' (default: uk)",
    )
    p.add_argument(
        "--cities",
        nargs="+",
        help="Override city list (e.g. --cities london manchester). "
             "Defaults to all cities for the selected site.",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=int(os.getenv("MAX_PAGES", 9)),
        help="Max listing pages per city (default: 9)",
    )
    p.add_argument(
        "--delay-min",
        type=float,
        default=float(os.getenv("REQUEST_DELAY_MIN", 2)),
        help="Min delay between requests in seconds (default: 2)",
    )
    p.add_argument(
        "--delay-max",
        type=float,
        default=float(os.getenv("REQUEST_DELAY_MAX", 5)),
        help="Max delay between requests in seconds (default: 5)",
    )
    p.add_argument(
        "--output-dir",
        default="output",
        help="Directory for CSV output (default: output/)",
    )
    p.add_argument(
        "--no-raw-html",
        action="store_true",
        help="Skip saving raw HTML responses",
    )
    p.add_argument(
        "--sheets",
        action="store_true",
        help="Also push results to Google Sheets (requires GOOGLE_SHEET_ID and "
             "GOOGLE_SHEETS_CREDENTIALS_JSON in .env)",
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

    logger.info("=== Treatwell Scraper Starting ===")
    logger.info(f"Site(s): {args.site} | Max pages: {args.max_pages} | "
                f"Delay: {args.delay_min}-{args.delay_max}s")

    if args.site == ["all"] or args.site == "all":
        sites_to_run = list(SITES.keys())
    else:
        sites_to_run = args.site

    all_venues = []
    for site_key in sites_to_run:
        cities = args.cities or None  # None = use defaults from SITES config
        for venue in scrape_site(
            site_key=site_key,
            cities=cities,
            max_pages=args.max_pages,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            save_html=not args.no_raw_html,
        ):
            all_venues.append(venue)

    if not all_venues:
        logger.warning("No venues scraped. Check logs for errors.")
        sys.exit(1)

    logger.info(f"Total venues scraped: {len(all_venues)}")

    # Export CSV (always)
    csv_path = export_csv(iter(all_venues), output_dir=args.output_dir)
    logger.info(f"CSV: {csv_path}")

    # Export to Google Sheets (optional)
    if args.sheets:
        sheet_id = os.getenv("GOOGLE_SHEET_ID")
        creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON")
        if not sheet_id or not creds_path:
            logger.error(
                "GOOGLE_SHEET_ID and GOOGLE_SHEETS_CREDENTIALS_JSON must be set in .env"
            )
        else:
            export_google_sheets(all_venues, sheet_id, creds_path)

    logger.info("=== Done ===")


if __name__ == "__main__":
    main()
