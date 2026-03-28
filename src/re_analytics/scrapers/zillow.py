"""Zillow listing scraper using their internal JSON API.

Uses httpx PUT to Zillow's `async-create-search-page-state` endpoint,
which returns structured JSON without needing a browser.

Rate limit: ~20-50 requests/day/IP without proxies.
"""

from __future__ import annotations

import logging
import re

import httpx

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

ZILLOW_API_URL = "https://www.zillow.com/async-create-search-page-state"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.zillow.com",
    "Referer": "https://www.zillow.com/",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

# Map our property types to Zillow filterState keys
# mf = multi-family, sf = single-family, con = condo, apa = apartment
FILTER_STATE_MAP = {
    PropertyType.MULTI_FAMILY: {"mf": {"value": True}, "sf": {"value": False}, "con": {"value": False}, "land": {"value": False}},
    PropertyType.SINGLE_FAMILY: {"sf": {"value": True}, "mf": {"value": False}, "con": {"value": False}, "land": {"value": False}},
    PropertyType.ANY: {},
}

MAX_PAGES = 5


class ZillowScraper(BaseScraper):
    """Scraper using Zillow's internal JSON API."""

    name = "zillow"

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

    def _build_request_body(self, criteria: SearchCriteria, page: int = 1) -> dict:
        """Build the JSON body for the Zillow API request."""
        city_state = f"{criteria.city}, {criteria.state}"

        filter_state: dict = {}

        # Price filter
        if criteria.min_price > 0:
            filter_state["price"] = {"min": criteria.min_price}
        if criteria.max_price < 999_999_999:
            price_filter = filter_state.get("price", {})
            price_filter["max"] = criteria.max_price
            filter_state["price"] = price_filter

        # Property type filter
        type_filters = FILTER_STATE_MAP.get(criteria.property_type, {})
        filter_state.update(type_filters)

        # Only active listings
        filter_state["isForSaleByAgent"] = {"value": True}
        filter_state["isForSaleByOwner"] = {"value": True}
        filter_state["isNewConstruction"] = {"value": False}
        filter_state["isForSaleForeclosure"] = {"value": False}
        filter_state["isComingSoon"] = {"value": False}
        filter_state["isAuction"] = {"value": False}

        search_query_state = {
            "usersSearchTerm": city_state,
            "filterState": filter_state,
            "isListVisible": True,
            "pagination": {},
        }

        if page > 1:
            search_query_state["pagination"] = {"currentPage": page}

        return {
            "searchQueryState": search_query_state,
            "wants": {
                "cat1": ["listResults"],
                "cat2": ["total"],
            },
            "requestId": page,
        }

    async def search(self, criteria: SearchCriteria) -> list[Listing]:
        client = self._get_client()
        all_listings: list[Listing] = []

        for page in range(1, MAX_PAGES + 1):
            body = self._build_request_body(criteria, page)

            if self.debug:
                logger.debug(f"Zillow API request page {page}: PUT {ZILLOW_API_URL}")

            try:
                resp = await client.put(ZILLOW_API_URL, json=body)
            except httpx.HTTPError as e:
                logger.warning(f"Zillow API request failed: {e}")
                break

            if self.debug:
                logger.debug(f"Zillow API response: status={resp.status_code}, size={len(resp.content)} bytes")

            if resp.status_code != 200:
                logger.warning(f"Zillow API returned {resp.status_code}")
                if self.debug:
                    logger.debug(f"Response body (first 500 chars): {resp.text[:500]}")
                break

            try:
                data = resp.json()
            except Exception:
                logger.warning("Zillow API returned non-JSON response")
                if self.debug:
                    logger.debug(f"Response body (first 500 chars): {resp.text[:500]}")
                break

            results = (
                data.get("cat1", {})
                .get("searchResults", {})
                .get("listResults", [])
            )

            if self.debug:
                logger.debug(f"Zillow page {page}: {len(results)} results in listResults")

            if not results:
                break

            for item in results:
                listing = self._parse_result(item, criteria)
                if listing:
                    all_listings.append(listing)

            # Check if there are more pages
            total_pages = (
                data.get("cat1", {})
                .get("searchResults", {})
                .get("totalPages", 1)
            )
            if page >= total_pages:
                break

        return all_listings

    def _parse_result(self, item: dict, criteria: SearchCriteria) -> Listing | None:
        """Parse a single Zillow search result into a Listing."""
        try:
            price = item.get("unformattedPrice") or item.get("price", 0)
            if isinstance(price, str):
                price = int(re.sub(r"[^\d]", "", price) or "0")

            address = item.get("address", "")
            detail_url = item.get("detailUrl", "")
            if detail_url and not detail_url.startswith("http"):
                detail_url = f"https://www.zillow.com{detail_url}"

            # Parse address for city/state/zip
            city = criteria.city
            state = criteria.state
            zip_code = ""
            if address:
                zip_match = re.search(r"(\d{5})", address)
                if zip_match:
                    zip_code = zip_match.group(1)

            # Get home info from nested data
            home_info = item.get("hdpData", {}).get("homeInfo", {})
            home_type = home_info.get("homeType", "")

            beds = item.get("beds")
            baths = item.get("baths")
            sqft = item.get("area")

            if not address and not price:
                return None

            return Listing(
                address=address,
                city=home_info.get("city", city),
                state=home_info.get("state", state),
                zip_code=home_info.get("zipcode", zip_code),
                price=int(price) if price else 0,
                sqft=int(sqft) if sqft else None,
                bedrooms=int(beds) if beds else None,
                bathrooms=float(baths) if baths else None,
                property_type=home_type,
                listing_url=detail_url,
                source=self.name,
                mls_number=str(item.get("zpid", "")),
            )
        except Exception as e:
            logger.debug(f"Failed to parse Zillow result: {e}")
            return None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
