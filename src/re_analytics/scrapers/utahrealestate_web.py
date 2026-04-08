"""UtahRealEstate.com website scraper using httpx + BeautifulSoup.

Lightweight scraper that fetches search results and detail pages from
utahrealestate.com without a browser. Falls back gracefully if blocked.

URL patterns and CSS selectors from carsonordyna/Stat_386_final_project.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx
from bs4 import BeautifulSoup

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://www.utahrealestate.com"


def _city_slug(city: str) -> str:
    """Convert city name to URL slug: 'Salt Lake City' → 'salt-lake-city'."""
    return city.strip().lower().replace(" ", "-")


class UtahRealEstateWebScraper(BaseScraper):
    """Lightweight scraper for utahrealestate.com public pages."""

    name = "utahrealestate-web"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=HEADERS,
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def search(self, criteria: SearchCriteria) -> list[Listing]:
        client = self._get_client()
        slug = _city_slug(criteria.city)
        search_url = f"{BASE_URL}/{slug}-homes"

        if self.debug:
            logger.debug(f"UtahRealEstate web: GET {search_url}")

        try:
            resp = await client.get(search_url)
        except httpx.HTTPError as e:
            logger.warning(f"UtahRealEstate web request failed: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(
                f"UtahRealEstate web returned {resp.status_code}. "
                "Site may require a browser — consider using utahrealestate-api source."
            )
            return []

        # Check for bot detection / captcha
        if "captcha" in resp.text.lower() or len(resp.text) < 1000:
            logger.warning(
                "UtahRealEstate web appears to be blocking requests. "
                "Consider using utahrealestate-api source with RESO credentials."
            )
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".property___card")

        if self.debug:
            logger.debug(f"UtahRealEstate web: found {len(cards)} property cards")

        if not cards:
            return []

        # Extract MLS numbers from cards and fetch detail pages
        mls_numbers = []
        for card in cards:
            mls = card.get("listno")
            if mls:
                mls_numbers.append(mls)

        if self.debug:
            logger.debug(f"UtahRealEstate web: {len(mls_numbers)} MLS numbers found")

        # Fetch detail pages concurrently (limit concurrency to 5)
        listings: list[Listing] = []
        semaphore = asyncio.Semaphore(5)

        async def fetch_detail(mls: str) -> Listing | None:
            async with semaphore:
                return await self._fetch_detail(client, mls, criteria)

        tasks = [fetch_detail(mls) for mls in mls_numbers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Listing):
                listings.append(result)
            elif isinstance(result, Exception):
                logger.debug(f"Detail fetch failed: {result}")

        return listings

    async def _fetch_detail(
        self, client: httpx.AsyncClient, mls: str, criteria: SearchCriteria
    ) -> Listing | None:
        """Fetch and parse a single listing detail page."""
        detail_url = f"{BASE_URL}/listing/{mls}"

        try:
            await asyncio.sleep(0.5)  # Rate limit
            resp = await client.get(detail_url)
        except httpx.HTTPError as e:
            logger.debug(f"Detail fetch failed for MLS {mls}: {e}")
            return None

        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        html = resp.text

        # Extract fields using CSS selectors from carsonordyna's scraper
        street = self._safe_text(soup, ".prop___overview h2")
        city_state = self._safe_text(soup, "#location-data")
        address = f"{street}, {city_state}".strip(", ")

        # Price, beds, baths, sqft from overview list items
        overview_items = soup.select(".prop-details-overview li span")
        price_str = overview_items[0].get_text(strip=True) if len(overview_items) > 0 else ""
        beds_str = overview_items[1].get_text(strip=True) if len(overview_items) > 1 else ""
        baths_str = overview_items[2].get_text(strip=True) if len(overview_items) > 2 else ""
        sqft_str = overview_items[3].get_text(strip=True) if len(overview_items) > 3 else ""

        price = self._parse_int(price_str)
        beds = self._parse_int(beds_str)
        baths = self._parse_float(baths_str)
        sqft = self._parse_int(sqft_str.replace(",", ""))

        # Year built from HTML via regex
        year_match = re.search(r"Year\s*Built[^0-9]*(\d{4})", html, re.I)
        year_built = int(year_match.group(1)) if year_match else None

        if not address or not price:
            return None

        # Parse city from city_state (format: "City, ST Zipcode")
        parsed_city = criteria.city
        parsed_zip = ""
        if city_state:
            parts = city_state.split(",")
            if parts:
                parsed_city = parts[0].strip()
            zip_match = re.search(r"(\d{5})", city_state)
            if zip_match:
                parsed_zip = zip_match.group(1)

        return Listing(
            address=address,
            city=parsed_city,
            state=criteria.state,
            zip_code=parsed_zip,
            price=price,
            sqft=sqft,
            bedrooms=beds,
            bathrooms=baths,
            year_built=year_built,
            listing_url=detail_url,
            source=self.name,
            mls_number=mls,
        )

    @staticmethod
    def _safe_text(soup: BeautifulSoup, selector: str) -> str:
        el = soup.select_one(selector)
        if el:
            return el.get_text(strip=True)
        return ""

    @staticmethod
    def _parse_int(s: str) -> int | None:
        cleaned = re.sub(r"[^\d]", "", s)
        return int(cleaned) if cleaned else None

    @staticmethod
    def _parse_float(s: str) -> float | None:
        cleaned = re.sub(r"[^\d.]", "", s)
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
