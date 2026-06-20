"""
Sync leads_master.csv and email send log to Google Sheets.
Run after each scrape/enrich cycle and after each send batch.

Usage:
    python3 -m scraper.sync_sheets
"""

import csv
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("treatwell.sheets")


LEADS_HEADERS = [
    "Country", "City", "Name", "Address", "Email", "Phone",
    "Rating", "Reviews",
    "Email 1", "Follow-up 1", "Follow-up 2", "Follow-up 3",
    "Replied", "Booking URL",
]
EMAIL_HEADERS = ["Date Sent", "Step", "Venue Name", "To Email", "Subject", "Status", "Replied"]

MASTER_CSV  = Path("output/leads_master.csv")
ENRICHED_CSV = Path("output/leads_master_enriched.csv")
EMAIL_LOG   = Path("output/email_log.csv")


def _get_sheet():
    import gspread
    from google.oauth2.service_account import Credentials

    creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON")
    sheet_id   = os.getenv("GOOGLE_SHEET_ID")

    if not creds_path or not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID and GOOGLE_SHEETS_CREDENTIALS_JSON must be set in .env")

    creds = Credentials.from_service_account_file(
        creds_path,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)


def sync_leads(sh) -> int:
    if not MASTER_CSV.exists():
        logger.warning(f"No master CSV at {MASTER_CSV}")
        return 0

    # Build email/phone lookup from enriched CSV (keyed by booking_url)
    enriched: dict[str, dict] = {}
    if ENRICHED_CSV.exists():
        with open(ENRICHED_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                url = row.get("booking_url", "")
                if url:
                    enriched[url] = row

    ws = sh.worksheet("Leads")
    ws.clear()
    ws.append_row(LEADS_HEADERS)

    rows = []
    with open(MASTER_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("booking_url", "")
            e = enriched.get(url, row)  # fall back to master row if not enriched
            def _date(col):
                ts = e.get(col, "")
                return ts[:10] if ts else ""  # "2026-06-20" or ""

            rows.append([
                row.get("country", ""),
                row.get("city", ""),
                row.get("name", ""),
                e.get("address", row.get("address", "")),
                e.get("email", ""),
                e.get("phone", ""),
                row.get("rating", ""),
                row.get("review_count", ""),
                _date("sent_at"),
                _date("follow_up_1_sent_at"),
                _date("follow_up_2_sent_at"),
                _date("follow_up_3_sent_at"),
                "Yes" if e.get("replied", "").lower() == "true" else "",
                url,
            ])

    if rows:
        ws.append_rows(rows, value_input_option="RAW")

    logger.info(f"Synced {len(rows)} leads to Sheets ({len(enriched)} with email data)")
    return len(rows)


def sync_emails(sh) -> int:
    if not EMAIL_LOG.exists():
        logger.info("No email log yet — skipping email tab sync")
        return 0

    ws = sh.worksheet("Emails Sent")
    ws.clear()
    ws.append_row(EMAIL_HEADERS)

    rows = []
    with open(EMAIL_LOG, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append([
                row.get("sent_at", ""),
                row.get("step", ""),
                row.get("venue_name", ""),
                row.get("to_email", ""),
                row.get("subject", ""),
                row.get("status", ""),
                row.get("replied", ""),
            ])

    if rows:
        ws.append_rows(rows, value_input_option="RAW")

    logger.info(f"Synced {len(rows)} email records to Sheets")
    return len(rows)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        sh = _get_sheet()
        leads = sync_leads(sh)
        emails = sync_emails(sh)
        print(f"Done — {leads} leads, {emails} email records synced.")
    except Exception as exc:
        logger.error(f"Sheets sync failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
