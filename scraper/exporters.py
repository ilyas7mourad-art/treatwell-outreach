"""
Export scraped venues to CSV and optionally Google Sheets.
"""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .scraper import Venue

logger = logging.getLogger("treatwell")

FIELDNAMES = [
    "site", "country", "city", "name", "address",
    "rating", "review_count", "booking_url", "treatwell_slug",
    "services_preview", "source_listing_url",
]


def export_csv(venues: Iterable[Venue], output_dir: str = "output") -> Path:
    Path(output_dir).mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(output_dir) / f"leads_{ts}.csv"

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        count = 0
        for v in venues:
            writer.writerow(v.to_dict())
            count += 1
            if count % 50 == 0:
                logger.info(f"Exported {count} rows...")
                f.flush()

    logger.info(f"CSV saved: {path} ({count} rows)")
    return path


def export_google_sheets(venues: list[Venue], spreadsheet_id: str, credentials_path: str) -> None:
    """Append rows to a Google Sheet. Requires gspread + service account JSON."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.error("gspread not installed. Run: pip install gspread google-auth")
        return

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.sheet1

    # Write header if sheet is empty
    if ws.row_count == 0 or not ws.get("A1"):
        ws.append_row(FIELDNAMES)

    rows = [[str(v.to_dict().get(f, "")) for f in FIELDNAMES] for v in venues]
    ws.append_rows(rows, value_input_option="RAW")
    logger.info(f"Pushed {len(rows)} rows to Google Sheets (ID: {spreadsheet_id})")
