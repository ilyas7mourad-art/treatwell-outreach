"""
Google Maps enricher — given a shop name and city, find phone and website.

Uses Playwright (headless Chromium) because Google Maps is fully JS-rendered.
Falls back through multiple selector strategies since Maps obfuscates its DOM.
"""

import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

logger = logging.getLogger("treatwell")

# Seconds to wait between Maps searches (Google rate-limits aggressively)
MAPS_DELAY_MIN = 4.0
MAPS_DELAY_MAX = 9.0


@dataclass
class MapsResult:
    phone: str = ""
    website: str = ""
    maps_url: str = ""
    enrich_status: str = "pending"


def _accept_cookies(page) -> None:
    """Dismiss Google cookie / consent dialogs common in EU."""
    consent_selectors = [
        'button:has-text("Accept all")',
        'button:has-text("Tout accepter")',
        'button:has-text("Accepter")',
        'button:has-text("Agree")',
        '[aria-label="Accept all"]',
        'form[action*="consent"] button',
    ]
    for sel in consent_selectors:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0:
                btn.click(timeout=2000)
                page.wait_for_load_state("networkidle", timeout=5000)
                return
        except Exception:
            pass


def _clean_phone(raw: str) -> str:
    """Strip Maps icon characters and whitespace from a raw phone string."""
    # Remove private-use Unicode (Powerline/icon glyphs like )
    cleaned = re.sub(r"[-]", "", raw)
    # Keep only digits, spaces, +, -, (, )
    m = re.search(r"(\+?[\d][\d\s\-\.\(\)]{6,}\d)", cleaned)
    return m.group(1).strip() if m else ""


def _extract_phone(page) -> str:
    """
    Try multiple selector strategies for the phone number in the Maps panel.
    Google Maps DOM is obfuscated; we layer strategies from most to least specific.
    """
    phone_pattern = re.compile(r"(\+?\d[\d\s\-\.\(\)]{6,}\d)")

    # Strategy 1: data-item-id containing "phone"
    try:
        for el in page.locator('[data-item-id*="phone"]').all():
            text = el.inner_text(timeout=1000).strip()
            cleaned = _clean_phone(text)
            if cleaned:
                return cleaned
    except Exception:
        pass

    # Strategy 2: aria-label on a button/div that looks like a phone number
    try:
        for el in page.locator('[aria-label]').all():
            label = el.get_attribute("aria-label") or ""
            m = phone_pattern.search(label)
            if m:
                return _clean_phone(m.group(1))
    except Exception:
        pass

    # Strategy 3: Full-text scan of the info panel for phone-like strings
    try:
        panel = page.locator('[role="main"]').first
        if panel.count() > 0:
            text = panel.inner_text(timeout=3000)
            for line in text.splitlines():
                line = line.strip()
                cleaned = _clean_phone(line)
                if cleaned and 7 <= len(re.sub(r"\D", "", cleaned)) <= 15:
                    return cleaned
    except Exception:
        pass

    return ""


def _extract_website(page) -> str:
    """Extract the business website URL from the Maps panel."""
    # Strategy 1: anchor with data-item-id="authority"
    try:
        el = page.locator('[data-item-id="authority"]').first
        if el.count() > 0:
            href = el.get_attribute("href") or ""
            if href.startswith("http"):
                return href
    except Exception:
        pass

    # Strategy 2: aria-label containing "Website" or "Site web"
    try:
        for el in page.locator('a[aria-label]').all():
            label = el.get_attribute("aria-label") or ""
            if any(k in label.lower() for k in ("website", "site web", "web site")):
                href = el.get_attribute("href") or ""
                if href.startswith("http"):
                    return href
    except Exception:
        pass

    # Strategy 3: look for a link that escapes Google's redirect wrapper
    # Maps wraps external links as /url?q=https://...
    try:
        for el in page.locator('a[href*="/url?q="]').all():
            href = el.get_attribute("href") or ""
            m = re.search(r"/url\?q=(https?://[^&]+)", href)
            if m:
                return m.group(1)
    except Exception:
        pass

    return ""


def _click_first_result(page) -> bool:
    """
    If Maps shows a list of results (no auto-selected place), click the first one.
    Returns True if a click was performed.
    """
    try:
        # Results list items
        first = page.locator('[role="feed"] a, [role="listitem"] a').first
        if first.count() > 0:
            first.click(timeout=3000)
            page.wait_for_load_state("networkidle", timeout=8000)
            return True
    except Exception:
        pass
    return False


def search_maps(page, name: str, city: str, country: str) -> MapsResult:
    """
    Navigate to Google Maps, search for the shop, and extract contact info.
    `page` is a Playwright Page object (caller manages the browser lifecycle).
    """
    result = MapsResult()

    # Build search query
    country_suffix = "UK" if country == "UK" else "France"
    query = f"{name} barber {city} {country_suffix}"
    url = f"https://www.google.com/maps/search/{quote_plus(query)}"

    try:
        logger.debug(f"Maps search: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        _accept_cookies(page)

        # Wait for either a detail panel or a results list
        try:
            page.wait_for_selector('[role="main"]', timeout=10000)
        except Exception:
            result.enrich_status = "timeout"
            return result

        # If we got a list, click the first result
        _click_first_result(page)

        # Extra wait for the detail panel to populate
        time.sleep(random.uniform(1.5, 2.5))

        result.maps_url = page.url
        result.phone = _extract_phone(page)
        result.website = _extract_website(page)
        result.enrich_status = "found" if (result.phone or result.website) else "not_found"

        logger.debug(
            f"  [{result.enrich_status}] {name!r}: phone={result.phone!r} web={result.website!r}"
        )

    except Exception as exc:
        logger.warning(f"Maps error for {name!r} in {city}: {exc}")
        result.enrich_status = "error"

    return result


def run_maps_enrichment(
    leads: list[dict],
    delay_min: float = MAPS_DELAY_MIN,
    delay_max: float = MAPS_DELAY_MAX,
    headless: bool = True,
) -> list[dict]:
    """
    Enrich a list of lead dicts with Maps data.
    Returns the same list with `phone`, `website`, `maps_url`, `enrich_status` added.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        raise

    enriched = []
    total = len(leads)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
            timezone_id="Europe/London",
        )
        # Mask automation signals
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        for i, lead in enumerate(leads, 1):
            name = lead.get("name", "").strip()
            city = lead.get("city", "").strip()
            country = lead.get("country", "UK")

            if not name or not city:
                lead["phone"] = ""
                lead["website"] = ""
                lead["maps_url"] = ""
                lead["enrich_status"] = "skipped"
                enriched.append(lead)
                continue

            logger.info(f"[{i}/{total}] Maps: {name!r}, {city}")
            maps_result = search_maps(page, name, city, country)

            lead["phone"] = maps_result.phone
            lead["website"] = maps_result.website
            lead["maps_url"] = maps_result.maps_url
            lead["enrich_status"] = maps_result.enrich_status
            enriched.append(lead)

            delay = random.uniform(delay_min, delay_max)
            logger.debug(f"  Sleeping {delay:.1f}s")
            time.sleep(delay)

        context.close()
        browser.close()

    return enriched
