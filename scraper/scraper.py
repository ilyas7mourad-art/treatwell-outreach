"""
Treatwell barbershop scraper — UK (treatwell.co.uk) and FR (treatwell.fr).

Listing pages are server-side rendered HTML. Falls back to Playwright for
pages that return near-empty bodies (JS-gated responses).
"""

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Generator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .utils import get_random_headers, random_delay, save_raw_html

logger = logging.getLogger("treatwell")

# ---------------------------------------------------------------------------
# Site configs
# ---------------------------------------------------------------------------

SITES = {
    "uk": {
        "base": "https://www.treatwell.co.uk",
        "listing_pattern": "/places/treatment-group-hair/at-barbershop/offer-type-local/in-{city}-uk/page-{page}/",
        "listing_first_page": "/places/treatment-group-hair/at-barbershop/offer-type-local/in-{city}-uk/",
        "cities": [
            "london", "manchester", "birmingham", "leeds", "glasgow",
            "sheffield", "bradford", "liverpool", "edinburgh", "bristol",
            "cardiff", "leicester", "nottingham", "coventry", "newcastle",
        ],
    },
    "fr": {
        "base": "https://www.treatwell.fr",
        "listing_pattern": "/salons/chez-barbier/offre-type-local/dans-{city}-france/page-{page}/",
        "listing_first_page": "/salons/chez-barbier/offre-type-local/dans-{city}-france/",
        "cities": [
            "paris", "lyon", "marseille", "bordeaux", "nantes",
            "toulouse", "nice", "strasbourg", "lille", "rennes",
        ],
    },
}

MIN_BODY_LENGTH = 5_000  # bytes — below this we assume JS block / bot detection


@dataclass
class Venue:
    site: str = ""
    country: str = ""
    city: str = ""
    name: str = ""
    address: str = ""
    rating: str = ""
    review_count: str = ""
    booking_url: str = ""
    treatwell_slug: str = ""
    services_preview: str = ""
    source_listing_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# HTTP session helpers
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(get_random_headers())
    return s


def fetch_html(session: requests.Session, url: str, retries: int = 3) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            session.headers.update(get_random_headers())
            resp = session.get(url, timeout=20, allow_redirects=True)
            if resp.status_code == 429:
                logger.warning(f"Rate limited on {url}, backing off 30s")
                import time; time.sleep(30)
                continue
            if resp.status_code == 403:
                logger.warning(f"403 Forbidden on {url} (attempt {attempt})")
                if attempt < retries:
                    random_delay(5, 12)
                    continue
                return None
            if resp.status_code != 200:
                logger.warning(f"HTTP {resp.status_code} for {url}")
                return None
            if len(resp.text) < MIN_BODY_LENGTH:
                logger.warning(f"Suspiciously short response ({len(resp.text)} chars) for {url}")
                if attempt < retries:
                    random_delay(8, 15)
                    continue
                return None
            return resp.text
        except requests.RequestException as exc:
            logger.error(f"Request error [{attempt}/{retries}] {url}: {exc}")
            if attempt < retries:
                random_delay(5, 10)
    return None


# ---------------------------------------------------------------------------
# Listing page parser
# ---------------------------------------------------------------------------

def parse_listing_page(html: str, site_key: str, city: str, listing_url: str) -> list[Venue]:
    """Extract venue cards from a listing page."""
    soup = BeautifulSoup(html, "lxml")
    venues: list[Venue] = []

    base_url = SITES[site_key]["base"]
    country = "UK" if site_key == "uk" else "FR"

    # Treatwell renders each card as a full <a> tag with an absolute URL.
    # href looks like: https://www.treatwell.co.uk/place/{slug}/?serviceIds=...
    # We match on the path segment only (after stripping the domain and query).
    place_re = re.compile(r"/place/([^/?&#]+)")

    seen_slugs: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]

        m = place_re.search(href)
        if not m:
            continue

        slug = m.group(1)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # Clean booking URL: strip query params so the slug is the canonical link
        clean_url = f"{base_url}/place/{slug}/"

        v = Venue(
            site=site_key,
            country=country,
            city=city.replace("-", " ").title(),
            booking_url=clean_url,
            treatwell_slug=slug,
            source_listing_url=listing_url,
        )

        # ── Name ─────────────────────────────────────────────────────────────
        # Observed: <h2 class="Text-module_mdHeader__...">Venue Name</h2>
        name_el = a_tag.find("h2")
        if name_el:
            v.name = name_el.get_text(strip=True)
        else:
            # fallback: first non-trivial text string in the card
            for s in a_tag.strings:
                clean = s.strip()
                if clean and len(clean) > 3:
                    v.name = clean
                    break

        # ── Rating ───────────────────────────────────────────────────────────
        # Observed: <span class="...Rating-module_label...">4.9</span>
        rating_el = a_tag.find("span", class_=re.compile(r"Rating-module_label|rating.*label|note", re.I))
        if rating_el:
            v.rating = rating_el.get_text(strip=True)
        else:
            # regex fallback on card text
            card_text = a_tag.get_text(separator="\n")
            rm = re.search(r"\b([45]\.[0-9]|[45],[0-9])\b", card_text)
            if rm:
                v.rating = rm.group(1).replace(",", ".")

        # ── Review count ─────────────────────────────────────────────────────
        # Observed: <span class="...BrowseResultRating-module--label...">1267 reviews</span>
        review_el = a_tag.find("span", class_=re.compile(r"BrowseResultRating|review|avis", re.I))
        if review_el:
            rev_text = review_el.get_text(strip=True)
            m2 = re.search(r"([\d,]+)", rev_text)
            if m2:
                v.review_count = m2.group(1).replace(",", "")
        else:
            card_text = a_tag.get_text(separator="\n")
            m2 = re.search(r"([\d,]+)\s*(?:reviews?|avis)", card_text, re.I)
            if m2:
                v.review_count = m2.group(1).replace(",", "")

        # ── Services preview ─────────────────────────────────────────────────
        card_text = a_tag.get_text(separator="\n")
        price_lines = [
            line.strip() for line in card_text.splitlines()
            if re.search(r"[£€]\s*\d+", line)
        ]
        if price_lines:
            v.services_preview = " | ".join(price_lines[:3])

        venues.append(v)

    return venues


# ---------------------------------------------------------------------------
# Venue detail page parser
# ---------------------------------------------------------------------------

def parse_venue_page(html: str, venue: Venue) -> Venue:
    """Enrich a Venue with data from its detail page."""
    import json as _json
    soup = BeautifulSoup(html, "lxml")

    # JSON-LD is the most reliable source — parse it first.
    # Treatwell uses {"@context":..., "@graph":[...]} wrapping.
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            candidates = []
            if isinstance(data, dict):
                # Unwrap @graph if present
                candidates = data.get("@graph", [data])
            elif isinstance(data, list):
                candidates = data
            for item in candidates:
                if isinstance(item, dict):
                    _extract_jsonld(item, venue)
        except Exception:
            pass

    # Fallback: h1 for name if JSON-LD didn't give us one
    if not venue.name:
        tag = soup.find("h1")
        if tag:
            venue.name = tag.get_text(strip=True)

    return venue


def _extract_jsonld(data: dict, venue: Venue) -> None:
    """Pull fields from a JSON-LD object into the venue."""
    if data.get("name") and not venue.name:
        venue.name = data["name"]

    addr = data.get("address", {})
    if isinstance(addr, dict) and not venue.address:
        parts = [
            addr.get("streetAddress", ""),
            addr.get("addressLocality", ""),
            addr.get("postalCode", ""),
        ]
        venue.address = ", ".join(p for p in parts if p)

    rating = data.get("aggregateRating", {})
    if isinstance(rating, dict) and not venue.rating:
        venue.rating = str(rating.get("ratingValue", ""))
        venue.review_count = str(rating.get("reviewCount", ""))


# ---------------------------------------------------------------------------
# Main scrape generator
# ---------------------------------------------------------------------------

def scrape_site(
    site_key: str,
    cities: list[str] | None = None,
    max_pages: int = 9,
    delay_min: float = 2.0,
    delay_max: float = 5.0,
    save_html: bool = True,
) -> Generator[Venue, None, None]:
    """
    Yield Venue objects for all barbershops found across listing pages,
    then enrich each with a detail-page request.
    """
    config = SITES[site_key]
    base = config["base"]
    target_cities = cities or config["cities"]
    session = make_session()

    for city in target_cities:
        logger.info(f"[{site_key.upper()}] Scraping city: {city}")

        for page in range(1, max_pages + 1):
            if page == 1:
                path = config["listing_first_page"].format(city=city)
            else:
                path = config["listing_pattern"].format(city=city, page=page)

            url = urljoin(base, path)
            logger.info(f"  Listing page {page}: {url}")

            html = fetch_html(session, url)
            if not html:
                logger.warning(f"  No HTML for {url}, stopping pagination for {city}")
                break

            if save_html:
                save_raw_html(html, f"{site_key}_{city}_page{page}")

            venues = parse_listing_page(html, site_key, city, url)
            logger.info(f"  Found {len(venues)} venues on page {page}")

            if not venues:
                logger.info(f"  No venues on page {page}, stopping pagination for {city}")
                break

            for venue in venues:
                random_delay(delay_min, delay_max)
                detail_html = fetch_html(session, venue.booking_url)
                if detail_html:
                    if save_html:
                        save_raw_html(detail_html, f"venue_{venue.treatwell_slug}")
                    venue = parse_venue_page(detail_html, venue)
                else:
                    logger.warning(f"  Could not fetch detail page: {venue.booking_url}")

                logger.info(f"  -> {venue.name!r} | {venue.city} | {venue.rating} | {venue.address[:40] if venue.address else 'no address'}")
                yield venue

            random_delay(delay_min, delay_max)
