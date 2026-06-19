"""
Merge a fresh scraper CSV into the master leads CSV, deduplicating by URL.
Existing rows (with send history, enrichment data, etc.) are preserved as-is.
Only net-new venues are appended.

Usage:
    python -m scraper.merge --new output/leads_20240101_090000.csv \
                             --master output/leads_master.csv
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

logger = logging.getLogger("treatwell")


def merge(new_path: str, master_path: str) -> int:
    """
    Merge new_path into master_path. Returns count of rows added.
    """
    master = Path(master_path)
    new = Path(new_path)

    if not new.exists():
        logger.error(f"New CSV not found: {new_path}")
        sys.exit(1)

    with open(new, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        new_fieldnames = list(reader.fieldnames or [])
        new_rows = list(reader)

    if not new_rows:
        logger.info("New CSV is empty — nothing to merge.")
        return 0

    if master.exists():
        with open(master, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            master_fieldnames = list(reader.fieldnames or [])
            existing_rows = list(reader)
        existing_urls = {r.get("url", "").strip() for r in existing_rows if r.get("url")}
    else:
        existing_rows = []
        existing_urls = set()
        master_fieldnames = new_fieldnames

    # Merge fieldnames: master fields first, then any new fields from new CSV
    merged_fieldnames = master_fieldnames.copy()
    for col in new_fieldnames:
        if col not in merged_fieldnames:
            merged_fieldnames.append(col)

    added = []
    for row in new_rows:
        url = row.get("url", "").strip()
        if url and url in existing_urls:
            continue
        added.append(row)

    if not added:
        logger.info(f"No new venues found (all {len(new_rows)} already in master).")
        return 0

    all_rows = existing_rows + added
    with open(master, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=merged_fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            writer.writerow({col: row.get(col, "") for col in merged_fieldnames})

    logger.info(f"Merged {len(added)} new venues into {master_path} (total: {len(all_rows)})")
    return len(added)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Merge new scraper CSV into master leads CSV.")
    p.add_argument("--new", required=True, help="Fresh scraper output CSV")
    p.add_argument("--master", required=True, help="Master leads CSV (created if absent)")
    args = p.parse_args()
    merge(args.new, args.master)


if __name__ == "__main__":
    main()
