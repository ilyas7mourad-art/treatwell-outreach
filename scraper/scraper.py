"""
Treatwell barbershop scraper — UK + 9 European countries.

UK uses requests (SSR). All other Treatwell sites are JS-rendered and use
Playwright for listing pages; detail pages are fetched with requests (JSON-LD
is embedded in the HTML source regardless of rendering mode).
"""

import atexit
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
        "country": "UK",
        "use_playwright": False,
        "venue_re": r"/place/([^/?&#]+)",
        "venue_path": "/place/",
        "listing_first_page": "/places/treatment-group-hair/at-barbershop/offer-type-local/in-{city}-uk/",
        "listing_pattern": "/places/treatment-group-hair/at-barbershop/offer-type-local/in-{city}-uk/page-{page}/",
        "cities": [
            "london", "manchester", "birmingham", "leeds", "glasgow",
            "liverpool", "edinburgh", "bristol", "sheffield", "newcastle",
            "nottingham", "leicester", "cardiff", "brighton", "coventry",
            "bradford", "reading", "oxford", "cambridge", "york",
            "southampton", "portsmouth", "wolverhampton", "derby", "hull",
            "exeter", "norwich", "stoke-on-trent", "middlesbrough",
            "sunderland", "aberdeen", "dundee", "swansea", "belfast",
        ],
    },
    "fr": {
        "base": "https://www.treatwell.fr",
        "country": "FR",
        "use_playwright": True,
        "venue_re": r"/salon/([^/?&#]+)",
        "venue_path": "/salon/",
        "listing_first_page": "/salons/chez-barbier/offre-type-local/dans-{city}-france/",
        "listing_pattern": "/salons/chez-barbier/offre-type-local/dans-{city}-france/page-{page}/",
        "cities": [
            "paris", "lyon", "marseille", "toulouse", "bordeaux",
            "lille", "nice", "nantes", "strasbourg", "rennes",
            "montpellier", "grenoble", "tours", "dijon", "angers",
            "caen", "saint-etienne", "rouen", "amiens", "clermont-ferrand",
            "metz", "besancon", "perpignan", "brest", "limoges",
            "nimes", "toulon", "villeurbanne", "aix-en-provence", "reims",
        ],
    },
    "de": {
        "base": "https://www.treatwell.de",
        "country": "DE",
        "use_playwright": True,
        "venue_re": r"/ort/([^/?&#]+)",
        "venue_path": "/ort/",
        "listing_first_page": "/orte/bei-barber-shop/in-{city}-de/",
        "listing_pattern": "/orte/bei-barber-shop/in-{city}-de/seite-{page}/",
        "cities": [
            "berlin", "hamburg", "munich", "cologne", "frankfurt",
            "stuttgart", "dusseldorf", "dortmund", "essen", "leipzig",
            "bremen", "dresden", "hannover", "nuremberg", "duisburg",
            "bochum", "wuppertal", "bielefeld", "bonn", "mannheim",
            "karlsruhe", "augsburg", "wiesbaden", "gelsenkirchen", "munster",
            "aachen", "braunschweig", "kiel", "chemnitz", "magdeburg",
            "halle", "freiburg", "erfurt", "rostock", "mainz",
            "lubeck", "oberhausen", "kassel", "saarbrucken", "potsdam",
        ],
    },
    "nl": {
        "base": "https://www.treatwell.nl",
        "country": "NL",
        "use_playwright": True,
        "venue_re": r"/salon/([^/?&#]+)",
        "venue_path": "/salon/",
        "listing_first_page": "/salons/bij-barbershop/in-{city}-nl/",
        "listing_pattern": "/salons/bij-barbershop/in-{city}-nl/pagina-{page}/",
        "cities": [
            "amsterdam", "rotterdam", "den-haag", "utrecht", "eindhoven",
            "tilburg", "groningen", "almere", "breda", "nijmegen",
            "haarlem", "arnhem", "zaandam", "amersfoort", "dordrecht",
            "leiden", "zwolle", "maastricht", "delft", "alkmaar",
            "deventer", "helmond", "enschede", "apeldoorn", "leeuwarden",
        ],
    },
    "be": {
        "base": "https://www.treatwell.be",
        "country": "BE",
        "use_playwright": True,
        "venue_re": r"/salon/([^/?&#]+)",
        "venue_path": "/salon/",
        "listing_first_page": "/salons/in-{city}-be/",
        "listing_pattern": "/salons/in-{city}-be/pagina-{page}/",
        "cities": [
            "brussel", "antwerpen", "gent", "brugge", "liege",
            "namur", "leuven", "aalst", "mechelen", "hasselt",
            "kortrijk", "ostend", "genk", "mons", "louvain",
        ],
    },
    "ch": {
        "base": "https://www.treatwell.ch",
        "country": "CH",
        "use_playwright": True,
        "venue_re": r"/ort/([^/?&#]+)",
        "venue_path": "/ort/",
        "listing_first_page": "/orte/bei-barber-shop/in-{city}-ch/",
        "listing_pattern": "/orte/bei-barber-shop/in-{city}-ch/seite-{page}/",
        "cities": [
            "zuerich", "geneve", "basel", "bern", "lausanne",
            "winterthur", "luzern", "st-gallen", "lugano", "biel",
            "thun", "bellinzona", "fribourg", "schaffhausen", "chur",
        ],
    },
    "at": {
        "base": "https://www.treatwell.at",
        "country": "AT",
        "use_playwright": True,
        "venue_re": r"/ort/([^/?&#]+)",
        "venue_path": "/ort/",
        "listing_first_page": "/orte/bei-barber-shop/in-{city}-at/",
        "listing_pattern": "/orte/bei-barber-shop/in-{city}-at/seite-{page}/",
        "cities": [
            "wien", "graz", "linz", "salzburg", "innsbruck",
            "klagenfurt", "villach", "wels", "st-polten", "dornbirn",
        ],
    },
    "it": {
        "base": "https://www.treatwell.it",
        "country": "IT",
        "use_playwright": True,
        "venue_re": r"/salone/([^/?&#]+)",
        "venue_path": "/salone/",
        "listing_first_page": "/saloni/in-{city}-it/",
        "listing_pattern": "/saloni/in-{city}-it/pagina-{page}/",
        "cities": [
            "milano", "roma", "napoli", "torino", "palermo",
            "genova", "bologna", "firenze", "bari", "catania",
            "venezia", "verona", "padova", "trieste", "brescia",
            "bergamo", "modena", "parma", "reggio-emilia", "perugia",
            "livorno", "cagliari", "messina", "taranto", "rimini",
            "salerno", "ferrara", "pisa", "ancona", "bologna",
        ],
    },
    "es": {
        "base": "https://www.treatwell.es",
        "country": "ES",
        "use_playwright": True,
        "venue_re": r"/establecimiento/([^/?&#]+)",
        "venue_path": "/establecimiento/",
        "listing_first_page": "/establecimientos/en-{city}-es/",
        "listing_pattern": "/establecimientos/en-{city}-es/pagina-{page}/",
        "cities": [
            "madrid", "barcelona", "sevilla", "valencia", "bilbao",
            "malaga", "zaragoza", "murcia", "palma", "alicante",
            "cordoba", "valladolid", "vigo", "gijon", "granada",
            "elche", "hospitalet-de-llobregat", "terrassa", "badalona",
            "sabadell", "cartagena", "jerez-de-la-frontera", "pamplona",
            "donostia-san-sebastian", "almeria", "santander", "burgos",
        ],
    },
    "pt": {
        "base": "https://www.treatwell.pt",
        "country": "PT",
        "use_playwright": True,
        "venue_re": r"/estabelecimento/([^/?&#]+)",
        "venue_path": "/estabelecimento/",
        "listing_first_page": "/estabelecimentos/oferta-tipolocal/em-{city}/",
        "listing_pattern": "/estabelecimentos/oferta-tipolocal/em-{city}/pagina-{page}/",
        "cities": [
            "lisboa", "porto", "braga", "coimbra", "setubal",
            "funchal", "aveiro", "viseu", "leiria", "evora",
            "faro", "guimaraes", "viana-do-castelo", "castelo-branco", "beja",
        ],
    },
}

MIN_BODY_LENGTH = 5_000

# ---------------------------------------------------------------------------
# Playwright browser — lazy singleton, closed at process exit
# ---------------------------------------------------------------------------

_pw_instance = None
_pw_browser = None


def _get_pw_browser():
    global _pw_instance, _pw_browser
    if _pw_browser is None:
        from playwright.sync_api import sync_playwright
        _pw_instance = sync_playwright().__enter__()
        _pw_browser = _pw_instance.chromium.launch(headless=True)
        atexit.register(_close_pw_browser)
    return _pw_browser


def _close_pw_browser():
    global _pw_instance, _pw_browser
    if _pw_browser:
        try:
            _pw_browser.close()
        except Exception:
            pass
        _pw_browser = None
    if _pw_instance:
        try:
            _pw_instance.__exit__(None, None, None)
        except Exception:
            pass
        _pw_instance = None


def fetch_html_playwright(url: str) -> str | None:
    try:
        browser = _get_pw_browser()
        page = browser.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=25000)
            return page.content()
        finally:
            page.close()
    except Exception as exc:
        logger.error(f"Playwright error for {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Requests-based fetch (used for UK listing pages + all detail pages)
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
                logger.warning(f"Short response ({len(resp.text)} chars) for {url}")
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
# Venue dataclass
# ---------------------------------------------------------------------------

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
# Listing page parser
# ---------------------------------------------------------------------------

def parse_listing_page(html: str, site_key: str, city: str, listing_url: str) -> list[Venue]:
    soup = BeautifulSoup(html, "lxml")
    venues: list[Venue] = []

    config = SITES[site_key]
    base_url = config["base"]
    country = config["country"]
    venue_re = re.compile(config["venue_re"])
    venue_path = config["venue_path"]

    seen_slugs: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]

        m = venue_re.search(href)
        if not m:
            continue

        slug = m.group(1)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        clean_url = f"{base_url}{venue_path}{slug}/"

        v = Venue(
            site=site_key,
            country=country,
            city=city.replace("-", " ").title(),
            booking_url=clean_url,
            treatwell_slug=slug,
            source_listing_url=listing_url,
        )

        name_el = a_tag.find("h2")
        if name_el:
            v.name = name_el.get_text(strip=True)
        else:
            for s in a_tag.strings:
                clean = s.strip()
                if clean and len(clean) > 3:
                    v.name = clean
                    break

        rating_el = a_tag.find("span", class_=re.compile(r"Rating-module_label|rating.*label|note", re.I))
        if rating_el:
            v.rating = rating_el.get_text(strip=True)
        else:
            card_text = a_tag.get_text(separator="\n")
            rm = re.search(r"\b([45]\.[0-9]|[45],[0-9])\b", card_text)
            if rm:
                v.rating = rm.group(1).replace(",", ".")

        review_el = a_tag.find("span", class_=re.compile(r"BrowseResultRating|review|avis|bewertung", re.I))
        if review_el:
            rev_text = review_el.get_text(strip=True)
            m2 = re.search(r"([\d,]+)", rev_text)
            if m2:
                v.review_count = m2.group(1).replace(",", "")
        else:
            card_text = a_tag.get_text(separator="\n")
            m2 = re.search(r"([\d,]+)\s*(?:reviews?|avis|bewertung)", card_text, re.I)
            if m2:
                v.review_count = m2.group(1).replace(",", "")

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
# Venue detail page parser (requests-based for all countries)
# ---------------------------------------------------------------------------

def parse_venue_page(html: str, venue: Venue) -> Venue:
    import json as _json
    soup = BeautifulSoup(html, "lxml")

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            candidates = []
            if isinstance(data, dict):
                candidates = data.get("@graph", [data])
            elif isinstance(data, list):
                candidates = data
            for item in candidates:
                if isinstance(item, dict):
                    _extract_jsonld(item, venue)
        except Exception:
            pass

    if not venue.name:
        tag = soup.find("h1")
        if tag:
            venue.name = tag.get_text(strip=True)

    return venue


def _extract_jsonld(data: dict, venue: Venue) -> None:
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
    max_pages: int = 50,
    delay_min: float = 2.0,
    delay_max: float = 5.0,
    save_html: bool = True,
) -> Generator[Venue, None, None]:
    config = SITES[site_key]
    base = config["base"]
    use_playwright = config["use_playwright"]
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

            if use_playwright:
                html = fetch_html_playwright(url)
            else:
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
                # Detail pages use requests for all countries (JSON-LD is in HTML source)
                detail_html = fetch_html(session, venue.booking_url)
                if detail_html:
                    if save_html:
                        save_raw_html(detail_html, f"venue_{venue.treatwell_slug}")
                    venue = parse_venue_page(detail_html, venue)
                else:
                    logger.warning(f"  Could not fetch detail page: {venue.booking_url}")

                logger.info(
                    f"  -> {venue.name!r} | {venue.city} | {venue.rating} | "
                    f"{venue.address[:40] if venue.address else 'no address'}"
                )
                yield venue

            random_delay(delay_min, delay_max)
