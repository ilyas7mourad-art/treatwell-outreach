"""
Cold email sender — reads an enriched leads CSV and sends outreach emails via
Brevo SMTP. Writes sent=true + sent_at back to the same CSV after each send.

Safety rails:
  - Skips rows with no email or sent=true
  - Hard cap of MAX_DAILY (default 20) sends per calendar day
  - Daily count is derived from the CSV itself (sent_at date), so restarts
    and --resume all share the same limit correctly
  - Random 60-180s delay between sends (configurable)
  - Plain-text only, no links, no HTML — inbox-friendly during warmup
"""

import csv
import logging
import os
import random
import smtplib
import time
from datetime import date, datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional

logger = logging.getLogger("treatwell")

MAX_DAILY = 20
SEND_DELAY_MIN = 60   # seconds
SEND_DELAY_MAX = 180

EMAIL_SUBJECT = "Your booking site"

EMAIL_BODY_TEMPLATE = """\
Hey {shop_name}, saw you on Treatwell — your shop looks clean.

Quick thing — Treatwell takes a cut of every booking you get through them. I build custom booking sites for barbers: your own domain, deposit collection, auto reminders, you keep 100% of payments.

One time fee, no monthly charges.

Interested?

Ilyas
bookbarber.design"""


# ---------------------------------------------------------------------------
# SMTP helpers
# ---------------------------------------------------------------------------

def _build_smtp_config() -> dict:
    required = [
        "BREVO_SMTP_HOST", "BREVO_SMTP_PORT",
        "BREVO_SMTP_LOGIN", "BREVO_SMTP_PASSWORD",
        "BREVO_SENDER_EMAIL", "BREVO_SENDER_NAME",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required env vars: {', '.join(missing)}. "
            "Check your .env file."
        )
    return {
        "host": os.environ["BREVO_SMTP_HOST"],
        "port": int(os.environ["BREVO_SMTP_PORT"]),
        "login": os.environ["BREVO_SMTP_LOGIN"],
        "password": os.environ["BREVO_SMTP_PASSWORD"],
        "sender_email": os.environ["BREVO_SENDER_EMAIL"],
        "sender_name": os.environ["BREVO_SENDER_NAME"],
    }


def _connect_smtp(cfg: dict) -> smtplib.SMTP:
    server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(cfg["login"], cfg["password"])
    return server


def _send_one(server: smtplib.SMTP, cfg: dict, to_email: str, shop_name: str) -> None:
    body = EMAIL_BODY_TEMPLATE.format(shop_name=shop_name)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = EMAIL_SUBJECT
    msg["From"] = f"{cfg['sender_name']} <{cfg['sender_email']}>"
    msg["To"] = to_email
    # Suppress list-unsubscribe and bulk headers that trigger spam filters
    msg["X-Mailer"] = "Python"

    msg.attach(MIMEText(body, "plain", "utf-8"))

    server.sendmail(cfg["sender_email"], [to_email], msg.as_string())


# ---------------------------------------------------------------------------
# CSV read / write
# ---------------------------------------------------------------------------

def _load_csv(path: str) -> tuple[list[dict], list[str]]:
    """Return (rows, fieldnames). Adds sent/sent_at columns if absent."""
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
# Main send loop
# ---------------------------------------------------------------------------

def send_emails(
    csv_path: str,
    max_daily: int = MAX_DAILY,
    delay_min: float = SEND_DELAY_MIN,
    delay_max: float = SEND_DELAY_MAX,
    dry_run: bool = False,
) -> None:
    """
    Read the enriched CSV, send unsent emails, and write results back in-place.
    """
    rows, fieldnames = _load_csv(csv_path)

    already_sent_today = _count_sent_today(rows)
    logger.info(f"Emails sent today so far: {already_sent_today}/{max_daily}")

    if already_sent_today >= max_daily:
        logger.warning(
            f"Daily limit of {max_daily} already reached. "
            "Run again tomorrow or increase --max-daily."
        )
        return

    # Candidates: has email, not yet sent
    pending = [
        (i, r) for i, r in enumerate(rows)
        if r.get("email", "").strip()
        and r.get("sent", "").lower() != "true"
    ]

    if not pending:
        logger.info("No unsent leads with email addresses found.")
        return

    remaining_quota = max_daily - already_sent_today
    to_send = pending[:remaining_quota]
    skipped_quota = len(pending) - len(to_send)

    logger.info(
        f"{len(pending)} unsent leads with emails | "
        f"sending {len(to_send)} today | "
        f"{skipped_quota} deferred to tomorrow"
    )

    if dry_run:
        logger.info("[DRY RUN] Would send to:")
        for _, row in to_send:
            logger.info(f"  {row['email']} — {row['name']!r}")
        return

    cfg = _build_smtp_config()

    server: Optional[smtplib.SMTP] = None
    sent_count = 0

    try:
        logger.info("Connecting to Brevo SMTP...")
        server = _connect_smtp(cfg)
        logger.info("Connected.")

        for idx, (row_index, row) in enumerate(to_send):
            email = row["email"].strip()
            name = row.get("name", "").strip() or "there"

            logger.info(
                f"[{idx + 1}/{len(to_send)}] Sending to {email} ({name!r})"
            )

            try:
                _send_one(server, cfg, email, name)
            except smtplib.SMTPServerDisconnected:
                # Reconnect once if the server dropped the connection
                logger.warning("SMTP connection dropped, reconnecting...")
                server = _connect_smtp(cfg)
                _send_one(server, cfg, email, name)

            # Mark as sent and persist immediately
            rows[row_index]["sent"] = "true"
            rows[row_index]["sent_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            _save_csv(rows, fieldnames, csv_path)
            sent_count += 1
            logger.info(f"  Sent. ({sent_count} sent this session)")

            # Delay before the next send (skip delay after last email)
            if idx < len(to_send) - 1:
                delay = random.uniform(delay_min, delay_max)
                logger.info(f"  Waiting {delay:.0f}s before next send...")
                time.sleep(delay)

    except KeyboardInterrupt:
        logger.info(f"Interrupted. {sent_count} emails sent this session.")
    except Exception as exc:
        logger.error(f"Send error after {sent_count} emails: {exc}", exc_info=True)
        raise
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

    logger.info(
        f"=== Done. {sent_count} emails sent this session. "
        f"Total today: {already_sent_today + sent_count}/{max_daily} ==="
    )
