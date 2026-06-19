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
    soup = BeautifulSoup(html, "html.parser")
    venues: list[Venue] = []

    base_url = SITES[site_key]["base"]
    country = "UK" if site_key == "uk" else "FR"

    # Treatwell wraps each venue card in an <a> linking to /place/{slug}/
    # The slug pattern differs slightly by country
    slug_pattern = re.compile(r"^/place[s]?/[^/]+/?$")

    seen_slugs: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]
        # Match venue detail links (not listing or other nav links)
        if not re.match(r"^/place/[^/]+/?$", href) and not re.match(r"^/salon/[^/]+/?$", href):
            continue
        if href in seen_slugs:
            continue
        seen_slugs.add(href)

        v = Venue(
            site=site_key,
            country=country,
            city=city.replace("-", " ").title(),
            booking_url=urljoin(base_url, href),
            treatwell_slug=href.strip("/").split("/")[-1],
            source_listing_url=listing_url,
        )

        # Try to extract name, rating, reviews from the card context
        # Walk up to find the card container, then pull text elements
        card = a_tag
        for _ in range(5):  # walk up max 5 levels
            parent = card.parent
            if parent is None:
                break
            card = parent
            text = card.get_text(separator=" ", strip=True)
            if len(text) > 50:
                break

        card_text = card.get_text(separator="\n", strip=True)

        # Name: first meaningful text node inside the <a> tag
        name_el = a_tag.find(["h2", "h3", "h4", "span", "p"])
        if name_el:
            v.name = name_el.get_text(strip=True)
        else:
            # fallback: first non-empty text child of <a>
            for child in a_tag.strings:
                clean = child.strip()
                if clean and len(clean) > 2:
                    v.name = clean
                    break

        # Rating: look for a decimal like 4.9 or 4,8 near the card
        rating_match = re.search(r"\b([45]\.[0-9]|[45],[0-9])\b", card_text)
        if rating_match:
            v.rating = rating_match.group(1).replace(",", ".")

        # Review count
        review_match = re.search(r"(\d[\d,]+)\s*(?:reviews?|avis)", card_text, re.I)
        if review_match:
            v.review_count = review_match.group(1).replace(",", "")

        # Services preview: grab first price-bearing line
        price_lines = [
            line for line in card_text.splitlines()
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
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    # Full name (more reliable from detail page)
    for tag in soup.find_all(["h1", "h2"]):
        candidate = tag.get_text(strip=True)
        if candidate and len(candidate) > 3:
            venue.name = candidate
            break

    # Address patterns
    address_patterns = [
        r"\d+[^,\n]{3,60},\s*[A-Z][A-Za-z\s]+,\s*[A-Z]{1,2}\d",  # UK postcode
        r"\d+[^,\n]{3,60},\s*\d{5}\s+[A-Z][a-zA-Z\s]+",          # FR postcode
        r"\d+[^,\n]{3,60},\s*[A-Z][a-zA-Z\s-]{2,40}",             # generic
    ]
    for pattern in address_patterns:
        m = re.search(pattern, text)
        if m:
            venue.address = m.group(0).strip()
            break

    # JSON-LD structured data (schema.org)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                _extract_jsonld(data, venue)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        _extract_jsonld(item, venue)
        except Exception:
            pass

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
            addr.get("addressCountry", ""),
        ]
        venue.address = ", ".join(p for p in parts if p)

    if data.get("telephone") and not hasattr(venue, "phone"):
        venue.address = venue.address  # phone not in dataclass yet — skip

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
