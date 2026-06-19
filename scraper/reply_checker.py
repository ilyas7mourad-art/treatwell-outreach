"""
Reply detection via IMAP.

Searches the configured inbox for messages FROM each lead email address.
If any message is found, the lead is considered to have replied and their
sequence stops.

Requires IMAP_HOST, IMAP_USER, IMAP_PASSWORD in .env.
IMAP_PORT defaults to 993 (SSL). Set IMAP_PORT=143 for STARTTLS.

If IMAP credentials are missing or the connection fails, reply detection
is skipped for that run and a warning is logged — sends are not blocked.
"""

import imaplib
import logging
import os

logger = logging.getLogger("treatwell")


def _imap_configured() -> bool:
    return all(os.getenv(k) for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"))


def check_for_replies(lead_emails: set[str]) -> set[str]:
    """
    Return the subset of lead_emails that have at least one message
    in the inbox sent FROM that address (i.e. they replied).

    Returns an empty set if IMAP is not configured or the connection fails.
    """
    if not lead_emails:
        return set()

    if not _imap_configured():
        logger.warning(
            "IMAP_HOST / IMAP_USER / IMAP_PASSWORD not set — skipping reply check. "
            "Add them to .env to enable reply detection."
        )
        return set()

    host     = os.environ["IMAP_HOST"]
    port     = int(os.environ.get("IMAP_PORT", 993))
    user     = os.environ["IMAP_USER"]
    password = os.environ["IMAP_PASSWORD"]

    replied: set[str] = set()

    try:
        logger.info(f"IMAP: connecting to {host}:{port} as {user}")
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, password)
        mail.select("INBOX", readonly=True)

        for addr in lead_emails:
            # IMAP SEARCH FROM matches the envelope sender
            status, data = mail.search(None, f'FROM "{addr}"')
            if status == "OK" and data and data[0]:
                msg_ids = data[0].split()
                if msg_ids:
                    replied.add(addr.lower())
                    logger.info(f"  Reply detected from {addr}")

        mail.close()
        mail.logout()
        logger.info(f"IMAP: checked {len(lead_emails)} addresses, {len(replied)} replied")

    except Exception as exc:
        logger.warning(f"IMAP check failed ({exc}) — skipping reply detection this run")

    return replied
