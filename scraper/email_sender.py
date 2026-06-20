"""
Cold email sender — 4-step sequence via Brevo API (POST /v3/smtp/email).

Sequence:
  step 0  initial      — send immediately
  step 1  follow_up_1  — day 3 after initial
  step 2  follow_up_2  — day 6 after initial
  step 3  follow_up_3  — day 9 after initial (final)

Each step only sends if:
  - The previous step has been sent
  - Enough days have elapsed since the initial send
  - No reply has been detected from that contact

CSV columns tracked per lead:
  sent / sent_at
  follow_up_1_sent / follow_up_1_sent_at
  follow_up_2_sent / follow_up_2_sent_at
  follow_up_3_sent / follow_up_3_sent_at
  replied / replied_at
"""

import csv
import logging
import os
import random
import time
from datetime import date, datetime, timezone

import requests

from .reply_checker import check_for_replies

logger = logging.getLogger("treatwell")

BREVO_API_URL  = "https://api.brevo.com/v3/smtp/email"
MAX_DAILY      = 20
SEND_DELAY_MIN = 60
SEND_DELAY_MAX = 180

# ---------------------------------------------------------------------------
# Email copy
# ---------------------------------------------------------------------------

_INITIAL_SUBJECT = "Treatwell is taking your money"
_INITIAL_BODY = """\
Hey {shop_name},

Checked your profile on Treatwell. Your work is clean.

But every booking they send you? They take a cut. Every single one.

I build barbers their own booking site. Your domain, your brand, deposits straight to you, zero commission.

One payment. You own it forever.

Worth a quick chat?

Ilyas

bookbarber.design"""

_FU1_SUBJECT = "Re: Treatwell is taking your money"
_FU1_BODY = """\
Just checking if this landed in the right place.

Ilyas"""

_FU2_SUBJECT = "Re: Treatwell is taking your money"
_FU2_BODY = """\
Quick question. How many bookings do you get through Treatwell monthly?

Asking because most barbers I talk to don't realise how much they're giving away until they do the math.

Ilyas"""

_FU3_SUBJECT = "Re: Treatwell is taking your money"
_FU3_BODY = """\
Last one from me.

If the timing's off, no worries. But if you ever want your own booking site with zero commission you know where to find me.

Ilyas

bookbarber.design"""

# ---------------------------------------------------------------------------
# Sequence definition
# Each dict describes one step in send order.
# day_offset is measured from the initial sent_at date.
# ---------------------------------------------------------------------------

SEQUENCE = [
    {
        "label":       "initial",
        "sent_col":    "sent",
        "sent_at_col": "sent_at",
        "day_offset":  0,
        "subject":     _INITIAL_SUBJECT,
        "body":        _INITIAL_BODY,
    },
    {
        "label":       "follow_up_1",
        "sent_col":    "follow_up_1_sent",
        "sent_at_col": "follow_up_1_sent_at",
        "day_offset":  3,
        "subject":     _FU1_SUBJECT,
        "body":        _FU1_BODY,
    },
    {
        "label":       "follow_up_2",
        "sent_col":    "follow_up_2_sent",
        "sent_at_col": "follow_up_2_sent_at",
        "day_offset":  6,
        "subject":     _FU2_SUBJECT,
        "body":        _FU2_BODY,
    },
    {
        "label":       "follow_up_3",
        "sent_col":    "follow_up_3_sent",
        "sent_at_col": "follow_up_3_sent_at",
        "day_offset":  9,
        "subject":     _FU3_SUBJECT,
        "body":        _FU3_BODY,
    },
]

# All CSV columns owned by the sender
_SEQUENCE_COLS = [col for step in SEQUENCE for col in (step["sent_col"], step["sent_at_col"])]
SENDER_COLS = _SEQUENCE_COLS + ["replied", "replied_at"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


ALLOWED_COUNTRIES = {"UK"}


def _next_step(row: dict) -> dict | None:
    """
    Return the next sequence step that should be sent for this row, or None.
    Steps are evaluated in order; the first unsent eligible step is returned.
    """
    if row.get("replied", "").lower() == "true":
        return None
    if not row.get("email", "").strip():
        return None
    if row.get("country", "").upper() not in ALLOWED_COUNTRIES:
        return None

    initial_sent_at = _parse_ts(row.get("sent_at", ""))
    now = datetime.now(timezone.utc)

    for step in SEQUENCE:
        if row.get(step["sent_col"], "").lower() == "true":
            continue  # already sent this step

        if step["day_offset"] == 0:
            return step  # initial — send immediately

        # Follow-up: require initial sent and enough time elapsed
        if not initial_sent_at:
            return None

        days_since_initial = (now - initial_sent_at).days
        if days_since_initial >= step["day_offset"]:
            return step

        return None  # Not yet time; later steps won't be due either

    return None  # All steps complete


# ---------------------------------------------------------------------------
# API
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


def _send_one(cfg: dict, to_email: str, shop_name: str, step: dict) -> None:
    body = step["body"].format(shop_name=shop_name) if "{shop_name}" in step["body"] else step["body"]
    payload = {
        "sender":      {"name": cfg["sender_name"], "email": cfg["sender_email"]},
        "to":          [{"email": to_email}],
        "subject":     step["subject"],
        "textContent": body,
        "trackOpens":  0,
        "trackClicks": 0,
    }
    headers = {
        "api-key":      cfg["api_key"],
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    resp = requests.post(BREVO_API_URL, json=payload, headers=headers, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"Brevo API error {resp.status_code}: {resp.text}")
    logger.debug(f"  Brevo: {resp.status_code} {resp.text}")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _load_csv(path: str) -> tuple[list[dict], list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for col in SENDER_COLS:
        if col not in fieldnames:
            fieldnames.append(col)
    for row in rows:
        for col in SENDER_COLS:
            row.setdefault(col, "")
    return rows, fieldnames


def _save_csv(rows: list[dict], fieldnames: list[str], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _count_sent_today(rows: list[dict]) -> int:
    """Count all emails sent today across every sequence step."""
    today = date.today().isoformat()
    total = 0
    for row in rows:
        for step in SEQUENCE:
            ts = row.get(step["sent_at_col"], "")
            if ts and ts.startswith(today):
                total += 1
    return total


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def send_test_email(to_email: str) -> None:
    """Send the initial email to to_email — no CSV, no daily limit."""
    cfg = _build_config()
    logger.info(f"Sending test via Brevo API to {to_email} ...")
    _send_one(cfg, to_email, shop_name="Test Barber", step=SEQUENCE[0])
    logger.info("Done.")


def send_emails(
    csv_path: str,
    max_daily: int = MAX_DAILY,
    delay_min: float = SEND_DELAY_MIN,
    delay_max: float = SEND_DELAY_MAX,
    dry_run: bool = False,
    skip_reply_check: bool = False,
) -> None:
    rows, fieldnames = _load_csv(csv_path)

    # ── Reply detection ───────────────────────────────────────────────────────
    if not skip_reply_check:
        # Only check leads that have been contacted but not yet marked replied
        to_check = {
            r["email"].lower()
            for r in rows
            if r.get("email", "").strip()
            and r.get("sent", "").lower() == "true"
            and r.get("replied", "").lower() != "true"
        }
        if to_check:
            new_replies = check_for_replies(to_check)
            if new_replies:
                now_str = _now_str()
                for row in rows:
                    if row.get("email", "").lower() in new_replies:
                        row["replied"]    = "true"
                        row["replied_at"] = now_str
                        logger.info(f"Marked replied: {row['email']}")
                _save_csv(rows, fieldnames, csv_path)

    # ── Daily limit check ─────────────────────────────────────────────────────
    already_sent_today = _count_sent_today(rows)
    logger.info(f"Emails sent today so far: {already_sent_today}/{max_daily}")
    if already_sent_today >= max_daily:
        logger.warning(f"Daily limit of {max_daily} reached. Run again tomorrow.")
        return

    # ── Build send queue ──────────────────────────────────────────────────────
    queue: list[tuple[int, dict, dict]] = []  # (row_index, row, step)
    for i, row in enumerate(rows):
        step = _next_step(row)
        if step:
            queue.append((i, row, step))

    if not queue:
        logger.info("No emails due. All sequences up to date.")
        return

    quota    = max_daily - already_sent_today
    to_send  = queue[:quota]
    deferred = len(queue) - len(to_send)
    logger.info(
        f"{len(queue)} emails due | sending {len(to_send)} today"
        + (f" | {deferred} deferred" if deferred else "")
    )

    if dry_run:
        logger.info("[DRY RUN] Would send:")
        for _, row, step in to_send:
            logger.info(f"  [{step['label']}] {row['email']} — {row.get('name', '')!r}")
        return

    cfg = _build_config()
    sent_count = 0

    try:
        for idx, (row_index, row, step) in enumerate(to_send):
            email = row["email"].strip()
            name  = row.get("name", "").strip() or "there"
            logger.info(f"[{idx + 1}/{len(to_send)}] [{step['label']}] {email} ({name!r})")

            _send_one(cfg, email, name, step)

            rows[row_index][step["sent_col"]]    = "true"
            rows[row_index][step["sent_at_col"]] = _now_str()
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
