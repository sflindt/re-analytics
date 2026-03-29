"""Zillow scraper using Playwright (headless Chrome).

Free and unlimited — uses a real browser so PerimeterX can't detect it.
Slower than Scrapfly (~5-10s per page) but costs nothing.
"""

from __future__ import annotations

import json
import logging
import re

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper
from re_analytics.scrapers.zillow import (
    CITY_COORDS,
    FILTER_MAP,
    ALL_HOMES_FILTER,
    _get_bounding_box,
    _geocode_city,
    _build_zillow_body,
)

logger = logging.getLogger(__name__)

ZILLOW_SEARCH_URL = "https://www.zillow.com/async-create-search-page-state"


class ZillowBrowserScraper(BaseScraper):
    """Scraper using Playwright headless Chrome — free and unlimited."""

    name = "zillow-browser"

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._browser = None
        self._playwright = None

    async def _ensure_browser(self):
        """Lazy-init Playwright browser."""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)

    async def search(self, criteria: SearchCriteria) -> list[Listing]:
        city_key = criteria.city.strip().lower()
        coords = CITY_COORDS.get(city_key)
        if not coords:
            coords = _geocode_city(criteria.city, criteria.state)
            if not coords:
                logger.warning(f"Could not geocode {criteria.city}, {criteria.state}")
                return []

        ne_lat, ne_long, sw_lat, sw_long = _get_bounding_box(
            coords[0], coords[1], criteria.search_area_sqmi
        )
        filter_state = FILTER_MAP.get(criteria.property_type, ALL_HOMES_FILTER).copy()
        min_price_arg = criteria.min_price if criteria.min_price > 0 else None
        max_price_arg = criteria.max_price if criteria.max_price < 999_999_999 else None
        search_value = f"{criteria.city}, {criteria.state}"

        await self._ensure_browser()
        context = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        all_listings: list[Listing] = []
        unique_zpids: set[str] = set()

        try:
            # Navigate to Zillow first to get cookies
            await page.goto("https://www.zillow.com/", wait_until="domcontentloaded", timeout=30000)

            for page_num in range(1, 3):  # Max 2 pages to be respectful
                body = _build_zillow_body(
                    search_value=search_value,
                    ne_lat=ne_lat,
                    ne_long=ne_long,
                    sw_lat=sw_lat,
                    sw_long=sw_long,
                    filter_state=filter_state,
                    page=page_num,
                    min_price=min_price_arg,
                    max_price=max_price_arg,
                )

                if self.debug:
                    logger.debug(f"Playwright Zillow page {page_num}: {search_value}")

                # Use page.evaluate to make the PUT request from within the browser context
                response_text = await page.evaluate("""
                    async (args) => {
                        const resp = await fetch(args.url, {
                            method: 'PUT',
                            headers: {
                                'Content-Type': 'application/json',
                                'Accept': '*/*',
                            },
                            body: JSON.stringify(args.body),
                        });
                        return await resp.text();
                    }
                """, {"url": ZILLOW_SEARCH_URL, "body": body})

                try:
                    data = json.loads(response_text)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Playwright: non-JSON response on page {page_num}")
                    break

                logger.debug(f"Playwright response keys: {list(data.keys())}")

                search_results = (
                    data.get("cat1", {}).get("searchResults", {})
                    or data.get("searchResults", {})
                    or data
                )
                if page_num == 1:
                    results = search_results.get("mapResults", []) or search_results.get("listResults", [])
                else:
                    results = search_results.get("listResults", [])

                if not results:
                    results = data.get("mapResults", []) or data.get("listResults", [])

                logger.debug(f"Playwright page {page_num}: {len(results)} results")

                if not results:
                    break

                initial_count = len(all_listings)
                for item in results:
                    zpid = str(item.get("zpid", ""))
                    if not zpid or zpid in unique_zpids:
                        continue
                    unique_zpids.add(zpid)
                    listing = self._parse_result(item, criteria)
                    if listing:
                        all_listings.append(listing)

                if len(all_listings) == initial_count:
                    break

        except Exception as e:
            logger.warning(f"Playwright scraper failed: {e}")
        finally:
            await context.close()

        return all_listings

    def _parse_result(self, item: dict, criteria: SearchCriteria) -> Listing | None:
        """Parse a Zillow result item — reuses same logic as Scrapfly scraper."""
        try:
            price = item.get("unformattedPrice") or item.get("price", 0)
            if isinstance(price, str):
                price = int(re.sub(r"[^\d]", "", price) or "0")

            address = item.get("address", "")
            detail_url = item.get("detailUrl", "")
            if detail_url and not detail_url.startswith("http"):
                detail_url = f"https://www.zillow.com{detail_url}"

            home_info = item.get("hdpData", {}).get("homeInfo", {})
            beds = item.get("beds")
            baths = item.get("baths")
            sqft = item.get("area")
            zpid = str(item.get("zpid", ""))

            days_on_zillow = None
            time_on_zillow = home_info.get("timeOnZillow")
            if time_on_zillow and isinstance(time_on_zillow, (int, float)):
                days_on_zillow = time_on_zillow / (1000 * 60 * 60 * 24)

            if not address and not price:
                return None

            status_map = {
                "FOR_SALE": "Active",
                "RECENTLY_SOLD": "Sold",
                "PENDING": "Pending",
            }
            raw_status = item.get("statusType", "")
            status = status_map.get(raw_status, raw_status)
            sold_price = home_info.get("soldPrice") or home_info.get("lastSoldPrice")
            year_built = home_info.get("yearBuilt")
            lot_size = home_info.get("lotAreaValue") or home_info.get("lotSize")

            return Listing(
                address=address,
                city=home_info.get("city") or item.get("addressCity") or criteria.city,
                state=home_info.get("state") or item.get("addressState") or criteria.state,
                zip_code=home_info.get("zipcode") or item.get("addressZipcode") or "",
                price=int(price) if price else 0,
                sqft=int(sqft) if sqft else None,
                bedrooms=int(beds) if beds else None,
                bathrooms=float(baths) if baths else None,
                property_type=home_info.get("homeType", ""),
                listing_url=detail_url,
                source=self.name,
                mls_number=zpid,
                zpid=zpid,
                zestimate=item.get("zestimate"),
                rent_zestimate=home_info.get("rentZestimate"),
                tax_assessed_value=home_info.get("taxAssessedValue"),
                latitude=home_info.get("latitude") or item.get("latLong", {}).get("latitude"),
                longitude=home_info.get("longitude") or item.get("latLong", {}).get("longitude"),
                days_on_market=days_on_zillow,
                status=status,
                sold_price=int(sold_price) if sold_price else None,
                year_built=int(year_built) if year_built else None,
                lot_size=float(lot_size) if lot_size else None,
                list_date=home_info.get("datePostedString"),
            )
        except Exception as e:
            logger.debug(f"Failed to parse browser result: {e}")
            return None

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
