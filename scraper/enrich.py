#!/usr/bin/env python3
"""
Enrichment pipeline — adds phone, website, and email to an existing leads CSV.

  Step 1: Google Maps search  → phone + website URL
  Step 2: Website crawl       → email address(es)
  Step 3: Merge back to CSV

Usage:
    python -m scraper.enrich --input output/leads_20240101_120000.csv
    python -m scraper.enrich --input output/leads.csv --skip-maps  # email-only
    python -m scraper.enrich --input output/leads.csv --maps-only  # no email crawl
    python -m scraper.enrich --input output/leads.csv --limit 50   # first N rows
    python -m scraper.enrich --input output/leads.csv --resume     # skip already-enriched rows
"""

import argparse
import csv
import logging
import os
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .email_finder import find_emails
from .maps_enricher import run_maps_enrichment
from .utils import setup_logging

ENRICHED_FIELDS = [
    "site", "country", "city", "name", "address",
    "rating", "review_count", "booking_url", "treatwell_slug",
    "services_preview", "source_listing_url",
    # enriched
    "phone", "website", "email", "maps_url", "enrich_status",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Enrich Treatwell leads CSV with phone, website, and email via Google Maps."
    )
    p.add_argument("--input", required=True, help="Input leads CSV path")
    p.add_argument("--output", help="Output enriched CSV path (default: input file + _enriched suffix)")
    p.add_argument("--limit", type=int, help="Only process the first N rows")
    p.add_argument("--skip-maps", action="store_true", help="Skip Google Maps step")
    p.add_argument("--maps-only", action="store_true", help="Skip email crawl step")
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip rows that already have enrich_status set (for restarting interrupted runs)",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run Playwright headless (default: True)",
    )
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument(
        "--maps-delay-min", type=float,
        default=float(os.getenv("MAPS_DELAY_MIN", 4)),
        help="Min delay between Maps searches (default: 4s)",
    )
    p.add_argument(
        "--maps-delay-max", type=float,
        default=float(os.getenv("MAPS_DELAY_MAX", 9)),
        help="Max delay between Maps searches (default: 9s)",
    )
    p.add_argument(
        "--email-delay-min", type=float, default=1.5,
        help="Min delay between website page fetches (default: 1.5s)",
    )
    p.add_argument(
        "--email-delay-max", type=float, default=3.5,
        help="Max delay between website page fetches (default: 3.5s)",
    )
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"),
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(rows: list[dict], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Build field list: known enriched fields first, then any extras from the data
    extra = [k for k in (rows[0].keys() if rows else []) if k not in ENRICHED_FIELDS]
    fields = ENRICHED_FIELDS + extra

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def default_output_path(input_path: str) -> str:
    p = Path(input_path)
    return str(p.parent / (p.stem + "_enriched" + p.suffix))


def main() -> None:
    args = parse_args()
    logger = setup_logging(level=args.log_level)

    input_path = args.input
    if not Path(input_path).exists():
        logger.error(f"Input file not found: {input_path}")
        sys.exit(1)

    output_path = args.output or default_output_path(input_path)

    rows = load_csv(input_path)
    if not rows:
        logger.error("Input CSV is empty.")
        sys.exit(1)

    if args.limit:
        rows = rows[: args.limit]

    # If resuming, split into already-done and pending
    if args.resume:
        pending = [r for r in rows if not r.get("enrich_status")]
        done = [r for r in rows if r.get("enrich_status")]
        logger.info(f"Resume mode: {len(done)} already enriched, {len(pending)} pending")
    else:
        pending = rows
        done = []

    if not pending:
        logger.info("All rows already enriched. Nothing to do.")
        write_csv(done, output_path)
        logger.info(f"Written to {output_path}")
        return

    logger.info(f"=== Enriching {len(pending)} leads ===")

    # ── Step 1: Google Maps ──────────────────────────────────────────────────
    if not args.skip_maps:
        logger.info("Step 1/2: Google Maps (phone + website)")
        pending = run_maps_enrichment(
            pending,
            delay_min=args.maps_delay_min,
            delay_max=args.maps_delay_max,
            headless=args.headless,
        )
    else:
        logger.info("Step 1/2: Skipping Maps")
        for r in pending:
            r.setdefault("phone", "")
            r.setdefault("website", "")
            r.setdefault("maps_url", "")
            r.setdefault("enrich_status", "skipped_maps")

    # Write intermediate results immediately so a crash doesn't lose Maps data
    all_rows = done + pending
    write_csv(all_rows, output_path)
    logger.info(f"Intermediate save → {output_path}")

    # ── Step 2: Email finder ─────────────────────────────────────────────────
    if not args.maps_only:
        logger.info("Step 2/2: Email finder (website crawl)")
        for i, row in enumerate(pending, 1):
            website = row.get("website", "").strip()
            if not website:
                row["email"] = ""
                logger.debug(f"  [{i}/{len(pending)}] No website for {row.get('name')!r}, skipping email")
                continue

            logger.info(f"  [{i}/{len(pending)}] Email crawl: {website}")
            emails = find_emails(
                website,
                delay_min=args.email_delay_min,
                delay_max=args.email_delay_max,
            )
            row["email"] = emails[0] if emails else ""
            if emails:
                logger.info(f"    Found: {', '.join(emails)}")
            else:
                logger.info(f"    No email found")

            # Save progress every 10 rows
            if i % 10 == 0:
                all_rows = done + pending
                write_csv(all_rows, output_path)
                logger.info(f"  Progress save: {i}/{len(pending)} email rows done")

            delay = random.uniform(args.email_delay_min, args.email_delay_max)
            time.sleep(delay)
    else:
        logger.info("Step 2/2: Skipping email crawl")
        for row in pending:
            row.setdefault("email", "")

    # ── Final export ─────────────────────────────────────────────────────────
    all_rows = done + pending
    write_csv(all_rows, output_path)

    enriched_count = sum(1 for r in pending if r.get("email") or r.get("phone"))
    logger.info(
        f"=== Done. {len(all_rows)} rows total | "
        f"{enriched_count}/{len(pending)} leads enriched with phone/email ===\n"
        f"Output: {output_path}"
    )


if __name__ == "__main__":
    main()
