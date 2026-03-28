"""Zillow listing scraper using Playwright.

Scrapes Zillow search results for nationwide property listings.
Zillow is fully JS-rendered, so we use a headless browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Map property types to Zillow URL segments
ZILLOW_TYPE_MAP = {
    PropertyType.MULTI_FAMILY: "multifamily",
    PropertyType.SINGLE_FAMILY: "houses",
    PropertyType.ANY: "",
}

# Delay between page loads to avoid detection
REQUEST_DELAY = 3.0
MAX_PAGES = 5


class ZillowScraper(BaseScraper):
    """Scraper for Zillow using Playwright headless browser."""

    name = "zillow"

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None

    async def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        return self._browser

    def _build_search_url(self, criteria: SearchCriteria, page: int = 1) -> str:
        city_slug = criteria.city.lower().replace(" ", "-")
        state_slug = criteria.state.lower()

        type_segment = ZILLOW_TYPE_MAP.get(criteria.property_type, "")

        base = f"https://www.zillow.com/{city_slug}-{state_slug}"
        if type_segment:
            base += f"/{type_segment}"

        # Build filter params
        params = []
        if criteria.min_price > 0:
            params.append(f"price%2F{criteria.min_price}_")
        else:
            params.append("price%2F_")
        if criteria.max_price < 999_999_999:
            params[-1] = params[-1].rstrip("_") if criteria.min_price > 0 else "price%2F"
            # Zillow uses searchQueryState in URL, but the simple path-based filters
            # are more reliable for basic searches

        if page > 1:
            base += f"/{page}_p"

        return base + "/"

    async def search(self, criteria: SearchCriteria) -> list[Listing]:
        browser = await self._ensure_browser()
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )

        try:
            return await self._search_with_context(context, criteria)
        finally:
            await context.close()

    async def _search_with_context(
        self, context: BrowserContext, criteria: SearchCriteria
    ) -> list[Listing]:
        listings: list[Listing] = []
        page = await context.new_page()

        for page_num in range(1, MAX_PAGES + 1):
            url = self._build_search_url(criteria, page_num)
            logger.info(f"Fetching Zillow page {page_num}: {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Failed to load Zillow page {page_num}: {e}")
                break

            # Save debug output
            if self.debug:
                await self._save_debug(page, f"zillow_page{page_num}")

            # Try to extract listings from the page's embedded JSON data
            # Zillow embeds search results in a script tag as __NEXT_DATA__
            new_listings = await self._extract_from_next_data(page, criteria)

            # Fallback: parse DOM directly
            if not new_listings:
                new_listings = await self._extract_from_dom(page, criteria)

            if not new_listings:
                logger.info(f"No listings found on page {page_num}, stopping.")
                break

            listings.extend(new_listings)

            # Check if there's a next page
            next_btn = await page.query_selector('a[rel="next"]')
            if not next_btn:
                break

            await asyncio.sleep(REQUEST_DELAY)

        await page.close()
        return listings

    async def _extract_from_next_data(
        self, page: Page, criteria: SearchCriteria
    ) -> list[Listing]:
        """Try to extract listings from Zillow's __NEXT_DATA__ JSON blob."""
        listings = []
        try:
            script = await page.query_selector('script#__NEXT_DATA__')
            if not script:
                return []

            raw = await script.inner_text()
            data = json.loads(raw)

            # Navigate the nested JSON structure to find search results
            props = data.get("props", {}).get("pageProps", {})
            search_results = (
                props.get("searchPageState", {})
                .get("cat1", {})
                .get("searchResults", {})
                .get("listResults", [])
            )

            for result in search_results:
                listing = self._parse_json_result(result, criteria)
                if listing:
                    listings.append(listing)

        except (json.JSONDecodeError, AttributeError, KeyError) as e:
            logger.debug(f"Could not parse __NEXT_DATA__: {e}")

        return listings

    async def _extract_from_dom(
        self, page: Page, criteria: SearchCriteria
    ) -> list[Listing]:
        """Fallback: extract listings by parsing the rendered DOM."""
        listings = []
        try:
            cards = await page.query_selector_all(
                'article[data-test="property-card"], '
                'li[class*="ListItem"] article, '
                'div[id="grid-search-results"] li article'
            )

            for card in cards:
                listing = await self._parse_dom_card(card, criteria)
                if listing:
                    listings.append(listing)

        except Exception as e:
            logger.debug(f"DOM extraction failed: {e}")

        return listings

    def _parse_json_result(self, result: dict, criteria: SearchCriteria) -> Listing | None:
        try:
            price = result.get("unformattedPrice") or result.get("price", 0)
            if isinstance(price, str):
                price = int(re.sub(r"[^\d]", "", price) or "0")

            address_str = result.get("address", "")
            detail_url = result.get("detailUrl", "")
            if detail_url and not detail_url.startswith("http"):
                detail_url = f"https://www.zillow.com{detail_url}"

            # Parse address components
            city = criteria.city
            state = criteria.state
            zip_code = ""
            if ", " in address_str:
                parts = address_str.rsplit(", ", 2)
                if len(parts) >= 2:
                    # Try to extract state and zip from last part
                    last = parts[-1]
                    zip_match = re.search(r"(\d{5})", last)
                    if zip_match:
                        zip_code = zip_match.group(1)

            beds = result.get("beds")
            baths = result.get("baths")
            sqft = result.get("area")

            return Listing(
                address=address_str,
                city=city,
                state=state,
                zip_code=zip_code,
                price=int(price) if price else 0,
                sqft=int(sqft) if sqft else None,
                bedrooms=int(beds) if beds else None,
                bathrooms=float(baths) if baths else None,
                property_type=result.get("hdpData", {}).get("homeInfo", {}).get("homeType", ""),
                listing_url=detail_url,
                source=self.name,
            )
        except Exception as e:
            logger.debug(f"Failed to parse Zillow JSON result: {e}")
            return None

    async def _parse_dom_card(self, card, criteria: SearchCriteria) -> Listing | None:
        try:
            # Price
            price_el = await card.query_selector(
                '[data-test="property-card-price"], span[class*="Price"]'
            )
            price_text = await price_el.inner_text() if price_el else "0"
            price = int(re.sub(r"[^\d]", "", price_text) or "0")

            # Address
            addr_el = await card.query_selector(
                '[data-test="property-card-addr"], address'
            )
            address = await addr_el.inner_text() if addr_el else ""

            # Link
            link_el = await card.query_selector('a[data-test="property-card-link"], a[href*="/homedetails/"]')
            href = await link_el.get_attribute("href") if link_el else ""
            if href and not href.startswith("http"):
                href = f"https://www.zillow.com{href}"

            # Beds/baths/sqft from the info line
            info_el = await card.query_selector(
                'ul[class*="StyledPropertyCardHomeDetailsList"], '
                'div[class*="property-card-data"]'
            )
            beds, baths, sqft = None, None, None
            if info_el:
                info_text = await info_el.inner_text()
                beds_match = re.search(r"(\d+)\s*bd", info_text, re.IGNORECASE)
                baths_match = re.search(r"(\d+\.?\d*)\s*ba", info_text, re.IGNORECASE)
                sqft_match = re.search(r"([\d,]+)\s*sqft", info_text, re.IGNORECASE)
                if beds_match:
                    beds = int(beds_match.group(1))
                if baths_match:
                    baths = float(baths_match.group(1))
                if sqft_match:
                    sqft = int(sqft_match.group(1).replace(",", ""))

            if not address and not price:
                return None

            return Listing(
                address=address.strip(),
                city=criteria.city,
                state=criteria.state,
                zip_code="",
                price=price,
                sqft=sqft,
                bedrooms=beds,
                bathrooms=baths,
                property_type="",
                listing_url=href,
                source=self.name,
            )
        except Exception as e:
            logger.debug(f"Failed to parse Zillow DOM card: {e}")
            return None

    async def _save_debug(self, page: Page, prefix: str) -> None:
        """Save screenshot and HTML dump for debugging."""
        debug_dir = Path("debug")
        debug_dir.mkdir(exist_ok=True)
        try:
            await page.screenshot(path=str(debug_dir / f"{prefix}.png"), full_page=True)
            html = await page.content()
            (debug_dir / f"{prefix}.html").write_text(html, encoding="utf-8")
            logger.info(f"Debug saved: debug/{prefix}.png and debug/{prefix}.html")
        except Exception as e:
            logger.warning(f"Failed to save debug output: {e}")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
