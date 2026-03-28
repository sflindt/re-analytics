"""Redfin listing scraper using the Stingray CSV/JSON API.

Redfin's undocumented but reliable API requires no authentication.
Just browser-like headers. Max 350 results per query.

Endpoints:
  CSV:  GET https://www.redfin.com/stingray/api/gis-csv?...
  JSON: GET https://www.redfin.com/stingray/api/gis?...&render=json
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re

import httpx

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

REDFIN_GIS_URL = "https://www.redfin.com/stingray/api/gis"
REDFIN_SEARCH_URL = "https://www.redfin.com/stingray/do/location-autocomplete"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.redfin.com/",
    "Origin": "https://www.redfin.com",
}

# Redfin property type codes (uipt parameter)
# 1=House, 2=Condo, 3=Townhouse, 4=Multi-family, 5=Land, 6=Other
UIPT_MAP = {
    PropertyType.MULTI_FAMILY: "4",
    PropertyType.SINGLE_FAMILY: "1",
    PropertyType.ANY: "1,2,3,4",
}

# status=9 means "Active" listings
ACTIVE_STATUS = "9"


class RedfinScraper(BaseScraper):
    """Scraper using Redfin's Stingray API."""

    name = "redfin"

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

    async def _lookup_region_id(self, city: str, state: str) -> tuple[str, str] | None:
        """Look up Redfin region_id for a city using autocomplete API.

        Returns (region_id, region_type) or None.
        """
        client = self._get_client()
        params = {
            "location": f"{city} {state}",
            "v": "2",
        }

        try:
            resp = await client.get(REDFIN_SEARCH_URL, params=params)
            if resp.status_code != 200:
                logger.warning(f"Redfin autocomplete returned {resp.status_code}")
                return None

            # Redfin prepends "{}&&" to JSON responses
            text = resp.text
            if text.startswith("{}&&"):
                text = text[4:]

            data = json.loads(text)
            sections = data.get("payload", {}).get("sections", [])

            for section in sections:
                for row in section.get("rows", []):
                    # Look for city-level matches (type 6 = city)
                    if row.get("type") == "6" or "city" in row.get("subName", "").lower():
                        region_id = str(row.get("id", ""))
                        region_type = str(row.get("type", "6"))
                        if region_id:
                            logger.debug(f"Found Redfin region: {row.get('name')} id={region_id} type={region_type}")
                            return region_id, region_type

            # Fallback: use first result
            for section in sections:
                for row in section.get("rows", []):
                    region_id = str(row.get("id", ""))
                    region_type = str(row.get("type", "6"))
                    if region_id:
                        return region_id, region_type

        except Exception as e:
            logger.warning(f"Redfin region lookup failed: {e}")

        return None

    async def search(self, criteria: SearchCriteria) -> list[Listing]:
        client = self._get_client()

        # First, look up the region ID for this city
        region_info = await self._lookup_region_id(criteria.city, criteria.state)
        if not region_info:
            logger.warning(f"Could not find Redfin region for {criteria.city}, {criteria.state}")
            return []

        region_id, region_type = region_info

        # Build the GIS CSV request
        params = {
            "al": "3",
            "region_id": region_id,
            "region_type": region_type,
            "num_homes": "350",
            "status": ACTIVE_STATUS,
            "uipt": UIPT_MAP.get(criteria.property_type, "1,2,3,4"),
            "v": "8",
        }

        if criteria.min_price > 0:
            params["min_price"] = str(criteria.min_price)
        if criteria.max_price < 999_999_999:
            params["max_price"] = str(criteria.max_price)

        csv_url = f"{REDFIN_GIS_URL}-csv"

        if self.debug:
            logger.debug(f"Redfin CSV request: GET {csv_url} params={params}")

        try:
            resp = await client.get(csv_url, params=params)
        except httpx.HTTPError as e:
            logger.warning(f"Redfin API request failed: {e}")
            return []

        if self.debug:
            logger.debug(f"Redfin response: status={resp.status_code}, size={len(resp.content)} bytes")

        if resp.status_code != 200:
            logger.warning(f"Redfin API returned {resp.status_code}")
            if self.debug:
                logger.debug(f"Response (first 500 chars): {resp.text[:500]}")
            return []

        return self._parse_csv(resp.text, criteria)

    def _parse_csv(self, csv_text: str, criteria: SearchCriteria) -> list[Listing]:
        """Parse Redfin CSV response into Listing objects."""
        listings = []

        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            for row in reader:
                listing = self._parse_csv_row(row, criteria)
                if listing:
                    listings.append(listing)
        except Exception as e:
            logger.warning(f"Failed to parse Redfin CSV: {e}")
            if self.debug:
                logger.debug(f"CSV content (first 500 chars): {csv_text[:500]}")

        return listings

    def _parse_csv_row(self, row: dict, criteria: SearchCriteria) -> Listing | None:
        """Parse a single CSV row into a Listing."""
        try:
            # Redfin CSV column names (may vary slightly)
            address = row.get("ADDRESS", row.get("address", ""))
            city = row.get("CITY", row.get("city", criteria.city))
            state = row.get("STATE OR PROVINCE", row.get("STATE", row.get("state", criteria.state)))
            zip_code = row.get("ZIP OR POSTAL CODE", row.get("ZIP", row.get("zip", "")))
            price_str = row.get("PRICE", row.get("price", "0"))
            beds_str = row.get("BEDS", row.get("beds", ""))
            baths_str = row.get("BATHS", row.get("baths", ""))
            sqft_str = row.get("SQUARE FEET", row.get("sqft", ""))
            year_built_str = row.get("YEAR BUILT", row.get("year_built", ""))
            prop_type = row.get("PROPERTY TYPE", row.get("property_type", ""))
            url = row.get("URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)",
                         row.get("URL", row.get("url", "")))
            dom_str = row.get("DAYS ON MARKET", row.get("dom", ""))
            mls = row.get("MLS#", row.get("mls", row.get("MLS", "")))

            price = _parse_int(price_str)
            if not price and not address:
                return None

            return Listing(
                address=address.strip(),
                city=city.strip(),
                state=state.strip(),
                zip_code=str(zip_code).strip(),
                price=price or 0,
                sqft=_parse_int(sqft_str),
                bedrooms=_parse_int(beds_str),
                bathrooms=_parse_float(baths_str),
                property_type=prop_type,
                year_built=_parse_int(year_built_str),
                days_on_market=_parse_int(dom_str),
                listing_url=url.strip() if url else "",
                source=self.name,
                mls_number=str(mls).strip() if mls else None,
            )
        except Exception as e:
            logger.debug(f"Failed to parse Redfin CSV row: {e}")
            return None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


def _parse_int(val) -> int | None:
    if not val:
        return None
    try:
        return int(float(str(val).replace(",", "").replace("$", "").strip()))
    except (ValueError, TypeError):
        return None


def _parse_float(val) -> float | None:
    if not val:
        return None
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None
