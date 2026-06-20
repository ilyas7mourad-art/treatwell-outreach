"""
SMS fallback sender via Brevo transactional SMS API.
Targets leads that have a phone number but no email address.

POST /v3/transactionalSMS/sms
Tracks: sms_sent / sms_sent_at / sms_replied in the CSV.
Same 20/day cap shared with the email sender.
"""

import csv
import logging
import os
import re
import time
from datetime import date, datetime, timezone

import requests

logger = logging.getLogger("treatwell")

BREVO_SMS_URL = "https://api.brevo.com/v3/transactionalSMS/sms"
MAX_DAILY     = 20
SEND_DELAY    = 5   # seconds between SMS sends (SMS is fast, no need for big delays)

SMS_SENDER       = "BookBarber"   # max 11 chars, alphanumeric
ALLOWED_COUNTRIES = {"UK"}
SMS_TEMPLATE = (
    "Hey {shop_name}, saw your shop on Treatwell. "
    "Treatwell takes a cut of every booking you get. "
    "I build barbers their own booking site, your domain, "
    "zero commission, one payment, you own it forever. "
    "Worth a quick chat? Ilyas"
)

SMS_COLS = ["sms_sent", "sms_sent_at"]


# ---------------------------------------------------------------------------
# Phone normalisation — Brevo requires E.164 (digits only, with country code)
# ---------------------------------------------------------------------------

def _normalise_phone(raw: str, default_country_code: str = "44") -> str | None:
    digits = re.sub(r"[^\d+]", "", raw.strip())

    if digits.startswith("+"):
        digits = digits[1:]
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_country_code + digits[1:]

    if len(digits) < 7 or len(digits) > 15:
        return None

    return digits


def _country_code_for_country(country: str) -> str:
    mapping = {
        "UK": "44", "FR": "33", "DE": "49", "NL": "31",
        "BE": "32", "CH": "41", "AT": "43", "IT": "39",
        "ES": "34", "PT": "351",
    }
    return mapping.get(country.upper(), "44")


# ---------------------------------------------------------------------------
# Brevo API call
# ---------------------------------------------------------------------------

def _send_sms(api_key: str, phone: str, message: str) -> None:
    payload = {
        "sender":    SMS_SENDER,
        "recipient": phone,
        "content":   message,
        "type":      "transactional",
    }
    headers = {
        "api-key":      api_key,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    resp = requests.post(BREVO_SMS_URL, json=payload, headers=headers, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"Brevo SMS error {resp.status_code}: {resp.text}")
    logger.debug(f"  Brevo SMS: {resp.status_code} {resp.text}")


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _load_csv(path: str) -> tuple[list[dict], list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for col in SMS_COLS:
        if col not in fieldnames:
            fieldnames.append(col)
    for row in rows:
        for col in SMS_COLS:
            row.setdefault(col, "")
    return rows, fieldnames


def _save_csv(rows: list[dict], fieldnames: list[str], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _count_sms_sent_today(rows: list[dict]) -> int:
    today = date.today().isoformat()
    return sum(1 for r in rows if r.get("sms_sent_at", "").startswith(today))



# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def send_sms(
    csv_path: str,
    max_daily: int = MAX_DAILY,
    dry_run: bool = False,
) -> None:
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        raise EnvironmentError("BREVO_API_KEY not set in .env")

    rows, fieldnames = _load_csv(csv_path)

    # SMS has its own independent daily cap (does not count emails)
    sms_today = _count_sms_sent_today(rows)
    quota     = max_daily - sms_today

    logger.info(f"SMS daily cap: {max_daily} | SMS sent today: {sms_today} | remaining: {quota}")

    if quota <= 0:
        logger.warning(f"SMS daily limit of {max_daily} reached. Run again tomorrow.")
        return

    # Build queue: phone but no email, not yet SMS-sent
    queue = []
    for i, row in enumerate(rows):
        has_email  = bool(row.get("email", "").strip())
        has_phone  = bool(row.get("phone", "").strip())
        sms_sent   = row.get("sms_sent", "").lower() == "true"
        replied    = row.get("replied", "").lower() == "true"

        country = row.get("country", "").upper()
        if has_email or not has_phone or sms_sent or replied:
            continue
        if country not in ALLOWED_COUNTRIES:
            continue

        country_code = _country_code_for_country(row.get("country", "UK"))
        phone = _normalise_phone(row["phone"], default_country_code=country_code)
        if not phone:
            logger.warning(f"  Skipping unparseable phone: {row['phone']} ({row.get('name')})")
            continue

        queue.append((i, row, phone))

    if not queue:
        logger.info("No SMS candidates (all phone-only leads already contacted or no valid numbers).")
        return

    to_send  = queue[:quota]
    deferred = len(queue) - len(to_send)
    logger.info(
        f"{len(queue)} SMS candidates | sending {len(to_send)} today"
        + (f" | {deferred} deferred to tomorrow" if deferred else "")
    )

    if dry_run:
        logger.info("[DRY RUN] Would send SMS to:")
        for _, row, phone in to_send:
            logger.info(f"  +{phone} — {row.get('name')}")
        return

    sent_count = 0
    try:
        for idx, (row_index, row, phone) in enumerate(to_send):
            name    = row.get("name", "").strip() or "there"
            message = SMS_TEMPLATE.format(shop_name=name)

            logger.info(f"[{idx + 1}/{len(to_send)}] SMS → +{phone} ({name!r})")

            _send_sms(api_key, phone, message)

            rows[row_index]["sms_sent"]    = "true"
            rows[row_index]["sms_sent_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            _save_csv(rows, fieldnames, csv_path)
            sent_count += 1
            logger.info(f"  Sent. ({sent_count} this session)")

            if idx < len(to_send) - 1:
                time.sleep(SEND_DELAY)

    except KeyboardInterrupt:
        logger.info(f"Interrupted. {sent_count} SMS sent this session.")
    except Exception as exc:
        logger.error(f"SMS send error after {sent_count} messages: {exc}", exc_info=True)
        raise

    logger.info(f"=== SMS done. {sent_count} sent | total today: {sms_today + sent_count}/{max_daily} ===")
