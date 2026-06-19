"""
Cold email sender — Brevo transactional email API (POST /v3/smtp/email).

Uses textContent (plain text) not htmlContent. No SMTP, no MIME assembly.
Writes sent=true + sent_at back to the CSV after each successful send.

Safety rails:
  - Skips rows with no email or sent=true
  - Hard cap of MAX_DAILY (default 20) sends per calendar day
  - Daily count derived from the CSV itself — safe across restarts
  - Random 60-180s delay between sends
"""

import csv
import json
import logging
import os
import random
import time
from datetime import date, datetime, timezone

import requests

logger = logging.getLogger("treatwell")

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

MAX_DAILY      = 20
SEND_DELAY_MIN = 60   # seconds
SEND_DELAY_MAX = 180

EMAIL_SUBJECT = "Your booking site"

EMAIL_BODY_TEMPLATE = """\
Hey {shop_name}, saw you on Treatwell - your shop looks clean.

Quick thing - Treatwell takes a cut of every booking you get through them. I build custom booking sites for barbers: your own domain, deposit collection, auto reminders, you keep 100% of payments.

One time fee, no monthly charges.

Interested?

Ilyas
bookbarber.design"""


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _build_config() -> dict:
    required = ["BREVO_API_KEY", "BREVO_SENDER_EMAIL", "BREVO_SENDER_NAME"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required env vars: {', '.join(missing)}. Check your .env file."
        )
    return {
        "api_key":      os.environ["BREVO_API_KEY"],
        "sender_email": os.environ["BREVO_SENDER_EMAIL"],
        "sender_name":  os.environ["BREVO_SENDER_NAME"],
    }


def _send_one(cfg: dict, to_email: str, shop_name: str) -> None:
    payload = {
        "sender":      {"name": cfg["sender_name"], "email": cfg["sender_email"]},
        "to":          [{"email": to_email}],
        "subject":     EMAIL_SUBJECT,
        "textContent": EMAIL_BODY_TEMPLATE.format(shop_name=shop_name),
    }
    headers = {
        "api-key":      cfg["api_key"],
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    resp = requests.post(BREVO_API_URL, json=payload, headers=headers, timeout=15)
    if not resp.ok:
        raise RuntimeError(
            f"Brevo API error {resp.status_code}: {resp.text}"
        )
    logger.debug(f"  Brevo response: {resp.status_code} {resp.text}")


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _load_csv(path: str) -> tuple[list[dict], list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "sent" not in fieldnames:
        fieldnames.append("sent")
    if "sent_at" not in fieldnames:
        fieldnames.append("sent_at")
    for row in rows:
        row.setdefault("sent", "")
        row.setdefault("sent_at", "")
    return rows, fieldnames


def _save_csv(rows: list[dict], fieldnames: list[str], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _count_sent_today(rows: list[dict]) -> int:
    today = date.today().isoformat()
    return sum(
        1 for r in rows
        if r.get("sent", "").lower() == "true"
        and (r.get("sent_at") or "").startswith(today)
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def send_test_email(to_email: str) -> None:
    """Send one email to to_email — no CSV, no daily limit."""
    cfg = _build_config()
    logger.info(f"Sending test via Brevo API to {to_email} ...")
    _send_one(cfg, to_email, shop_name="Test Barber")
    logger.info("Done.")


def send_emails(
    csv_path: str,
    max_daily: int = MAX_DAILY,
    delay_min: float = SEND_DELAY_MIN,
    delay_max: float = SEND_DELAY_MAX,
    dry_run: bool = False,
) -> None:
    rows, fieldnames = _load_csv(csv_path)

    already_sent_today = _count_sent_today(rows)
    logger.info(f"Emails sent today so far: {already_sent_today}/{max_daily}")

    if already_sent_today >= max_daily:
        logger.warning(
            f"Daily limit of {max_daily} already reached. "
            "Run again tomorrow or increase --max-daily."
        )
        return

    pending = [
        (i, r) for i, r in enumerate(rows)
        if r.get("email", "").strip()
        and r.get("sent", "").lower() != "true"
    ]
    if not pending:
        logger.info("No unsent leads with email addresses found.")
        return

    quota     = max_daily - already_sent_today
    to_send   = pending[:quota]
    deferred  = len(pending) - len(to_send)
    logger.info(
        f"{len(pending)} unsent leads | sending {len(to_send)} today"
        + (f" | {deferred} deferred to tomorrow" if deferred else "")
    )

    if dry_run:
        logger.info("[DRY RUN] Would send to:")
        for _, row in to_send:
            logger.info(f"  {row['email']} - {row['name']!r}")
        return

    cfg = _build_config()
    sent_count = 0

    try:
        for idx, (row_index, row) in enumerate(to_send):
            email = row["email"].strip()
            name  = row.get("name", "").strip() or "there"
            logger.info(f"[{idx + 1}/{len(to_send)}] {email} ({name!r})")

            _send_one(cfg, email, name)

            rows[row_index]["sent"]    = "true"
            rows[row_index]["sent_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            _save_csv(rows, fieldnames, csv_path)
            sent_count += 1
            logger.info(f"  Sent. ({sent_count} this session)")

            if idx < len(to_send) - 1:
                delay = random.uniform(delay_min, delay_max)
                logger.info(f"  Waiting {delay:.0f}s ...")
                time.sleep(delay)

    except KeyboardInterrupt:
        logger.info(f"Interrupted. {sent_count} sent this session.")
    except Exception as exc:
        logger.error(f"Send error after {sent_count} emails: {exc}", exc_info=True)
        raise

    logger.info(
        f"=== Done. {sent_count} sent this session | "
        f"total today: {already_sent_today + sent_count}/{max_daily} ==="
    )
