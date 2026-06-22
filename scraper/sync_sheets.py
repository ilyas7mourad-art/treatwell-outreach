"""
Sync leads_master.csv + WhatsApp send data to Google Sheets.

Tabs:
  Leads       — all scraped leads (all countries) + WA send status
  WhatsApp    — UK leads that have been WhatsApp-contacted
  Contacted   — legacy email/SMS contacted leads

Usage:
    python3 -m scraper.sync_sheets
"""

import csv
import logging
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("treatwell.sheets")

LEADS_HEADERS = [
    "Country", "City", "Name", "Phone", "Email", "Rating", "Reviews",
    "WA Sent", "WA Follow-up 1", "WA Follow-up 2", "WA Follow-up 3",
    "WA Opted Out", "Booking URL",
]

WA_HEADERS = [
    "Country", "City", "Name", "Phone",
    "WA Sent", "WA Follow-up 1", "WA Follow-up 2", "WA Follow-up 3",
    "Opted Out", "Booking URL",
]

MASTER_CSV   = Path("output/leads_master.csv")
ENRICHED_CSV = Path("output/leads_master_enriched.csv")
DB_PATH      = Path("output/leads.db")


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


def _load_wa_data() -> dict:
    """Load WhatsApp send history from SQLite, keyed by booking_url."""
    if not DB_PATH.exists():
        return {}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            l.booking_url,
            l.phone,
            l.do_not_contact,
            l.dnc_at,
            MAX(CASE WHEN s.follow_up_num = 0 AND s.status = 'sent' THEN substr(s.sent_at,1,10) END) AS wa_sent,
            MAX(CASE WHEN s.follow_up_num = 1 AND s.status = 'sent' THEN substr(s.sent_at,1,10) END) AS wa_fu1,
            MAX(CASE WHEN s.follow_up_num = 2 AND s.status = 'sent' THEN substr(s.sent_at,1,10) END) AS wa_fu2,
            MAX(CASE WHEN s.follow_up_num = 3 AND s.status = 'sent' THEN substr(s.sent_at,1,10) END) AS wa_fu3
        FROM leads l
        LEFT JOIN sends s ON s.lead_id = l.id AND s.channel = 'whatsapp'
        GROUP BY l.id
    """).fetchall()
    conn.close()

    return {r["booking_url"]: dict(r) for r in rows if r["booking_url"]}


def _load_enriched() -> dict:
    """Load phone numbers from enriched CSV, keyed by booking_url."""
    enriched = {}
    if not ENRICHED_CSV.exists():
        return enriched
    _KEEP = ["email", "phone", "address"]
    with open(ENRICHED_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = row.get("booking_url", "")
            if not url:
                continue
            if url not in enriched:
                enriched[url] = row
            else:
                for col in _KEEP:
                    if row.get(col, "").strip() and not enriched[url].get(col, "").strip():
                        enriched[url][col] = row[col]
    return enriched


def _get_or_create_ws(sh, name, rows=20000, cols=12):
    try:
        return sh.worksheet(name)
    except Exception:
        return sh.add_worksheet(name, rows=rows, cols=cols)


def sync_leads(sh, enriched: dict, wa: dict) -> int:
    if not MASTER_CSV.exists():
        logger.warning("No master CSV found")
        return 0

    ws = _get_or_create_ws(sh, "Leads")
    ws.clear()
    ws.append_row(LEADS_HEADERS)
    ws.freeze(rows=1)

    rows = []
    with open(MASTER_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url  = row.get("booking_url", "")
            e    = enriched.get(url, {})
            w    = wa.get(url, {})
            phone = e.get("phone", "") or w.get("phone", "")

            rows.append([
                row.get("country", ""),
                row.get("city", ""),
                row.get("name", ""),
                phone,
                e.get("email", ""),
                row.get("rating", ""),
                row.get("review_count", ""),
                w.get("wa_sent", "") or "",
                w.get("wa_fu1", "")  or "",
                w.get("wa_fu2", "")  or "",
                w.get("wa_fu3", "")  or "",
                "Yes" if w.get("do_not_contact") else "",
                url,
            ])

    if rows:
        ws.append_rows(rows, value_input_option="RAW")

    logger.info(f"Leads tab: {len(rows)} rows ({len(wa)} with WA data)")
    return len(rows)


def sync_whatsapp(sh, enriched: dict, wa: dict) -> int:
    """Tab showing only leads that have been contacted on WhatsApp."""
    ws = _get_or_create_ws(sh, "WhatsApp")
    ws.clear()
    ws.append_row(WA_HEADERS)
    ws.freeze(rows=1)

    # Build a lookup of master CSV rows by URL for country/city/name
    master = {}
    if MASTER_CSV.exists():
        with open(MASTER_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                master[row.get("booking_url", "")] = row

    rows = []
    for url, w in wa.items():
        if not w.get("wa_sent"):
            continue
        m = master.get(url, {})
        e = enriched.get(url, {})
        phone = e.get("phone", "") or w.get("phone", "")

        rows.append([
            m.get("country", "UK"),
            m.get("city", ""),
            m.get("name", ""),
            phone,
            w.get("wa_sent", "")  or "",
            w.get("wa_fu1", "")   or "",
            w.get("wa_fu2", "")   or "",
            w.get("wa_fu3", "")   or "",
            "Yes" if w.get("do_not_contact") else "",
            url,
        ])

    # Sort: opted-out last, then by most recent send date
    rows.sort(key=lambda r: (r[8] == "Yes", r[4]), reverse=False)

    if rows:
        ws.append_rows(rows, value_input_option="RAW")

    logger.info(f"WhatsApp tab: {len(rows)} contacted leads")
    return len(rows)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        sh       = _get_sheet()
        enriched = _load_enriched()
        wa       = _load_wa_data()

        leads = sync_leads(sh, enriched, wa)
        wa_n  = sync_whatsapp(sh, enriched, wa)

        print(f"Done — {leads} leads synced, {wa_n} WhatsApp contacts shown.")
    except Exception as exc:
        logger.error(f"Sheets sync failed: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
