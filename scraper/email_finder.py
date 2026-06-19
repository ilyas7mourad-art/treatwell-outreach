"""
Email finder — given a business website URL, crawl it for email addresses.

Checks: homepage → /contact → /about → /info pages.
Extracts mailto: links first (most reliable), then falls back to regex scan.
"""

import logging
import re
import time
import random
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .utils import get_random_headers

logger = logging.getLogger("treatwell")

EMAIL_RE = re.compile(
    r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"
)

# Substrings that flag a non-contact email (skip these)
NOISE_PATTERNS = re.compile(
    r"@example\.|@domain\.|@your|noreply|no-reply|donotreply|"
    r"@sentry\.|@treatwell\.|@fresha\.|@booksy\.|@vagaro\.|"
    r"@pixelated|@2x\.|\.png@|\.jpg@|\.gif@|wixpress|squarespace|"
    r"@schema\.|@w3\.",
    re.I,
)

# Sub-pages likely to contain contact emails
CONTACT_PATHS = [
    "/contact", "/contact-us", "/contactus", "/contact.html",
    "/about", "/about-us", "/aboutus",
    "/info", "/reach-us", "/get-in-touch",
    "/nous-contacter", "/contact.php", "/kontakt",
]

REQUEST_TIMEOUT = 10
MAX_CRAWL_PAGES = 4  # homepage + up to 3 sub-pages


def _clean_email(raw: str) -> str:
    return raw.strip().lower()


def _is_valid_email(email: str) -> bool:
    if NOISE_PATTERNS.search(email):
        return False
    local, _, domain = email.partition("@")
    if len(local) < 2 or len(domain) < 4:
        return False
    if "." not in domain:
        return False
    return True


def _extract_emails_from_html(html: str) -> list[str]:
    """Find emails via mailto: links and raw text scan."""
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()

    # mailto: links are most reliable
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip()
            if EMAIL_RE.match(email):
                found.add(_clean_email(email))

    # Text scan — slower but catches obfuscated or plain-text emails
    text = soup.get_text(separator=" ")
    for match in EMAIL_RE.finditer(text):
        found.add(_clean_email(match.group(0)))

    return [e for e in found if _is_valid_email(e)]


def _fetch(session: requests.Session, url: str) -> Optional[str]:
    try:
        session.headers.update(get_random_headers())
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
        logger.debug(f"  email_finder: HTTP {resp.status_code} for {url}")
    except Exception as exc:
        logger.debug(f"  email_finder: fetch error {url}: {exc}")
    return None


def _same_origin(base: str, url: str) -> bool:
    b = urlparse(base)
    u = urlparse(url)
    return b.netloc == u.netloc


def find_emails(website_url: str, delay_min: float = 1.5, delay_max: float = 3.5) -> list[str]:
    """
    Crawl the business website for email addresses.
    Returns a deduplicated, noise-filtered list sorted by specificity
    (non-generic emails first).
    """
    if not website_url or not website_url.startswith("http"):
        return []

    session = requests.Session()
    base = website_url.rstrip("/")
    all_emails: set[str] = set()
    pages_checked = 0

    # 1. Homepage
    html = _fetch(session, base)
    if html:
        emails = _extract_emails_from_html(html)
        all_emails.update(emails)
        pages_checked += 1

        if not all_emails:
            # Look for contact/about links on the homepage
            soup = BeautifulSoup(html, "lxml")
            candidate_links: list[str] = []
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                full = urljoin(base, href)
                if not _same_origin(base, full):
                    continue
                path = urlparse(full).path.lower()
                if any(cp in path for cp in CONTACT_PATHS):
                    candidate_links.append(full)

            # Also check known contact paths directly
            for cp in CONTACT_PATHS:
                candidate_links.append(base + cp)

            seen_links: set[str] = set()
            for link in candidate_links:
                if pages_checked >= MAX_CRAWL_PAGES:
                    break
                if link in seen_links:
                    continue
                seen_links.add(link)

                time.sleep(random.uniform(delay_min, delay_max))
                sub_html = _fetch(session, link)
                if sub_html:
                    sub_emails = _extract_emails_from_html(sub_html)
                    all_emails.update(sub_emails)
                    pages_checked += 1
                    if all_emails:
                        break

    # Sort: non-generic first (not info@, contact@, hello@)
    generic_prefixes = ("info@", "contact@", "hello@", "bonjour@", "mail@", "enquiries@", "enquiry@")
    result = sorted(
        all_emails,
        key=lambda e: (any(e.startswith(p) for p in generic_prefixes), e),
    )
    return result
