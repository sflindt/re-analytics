"""UtahRealEstate.com scraper using Playwright headless browser.

The site is a JS-rendered SPA that blocks direct HTTP requests.
We use Playwright to automate a real browser session.

Search URLs:
  - Multi-family: https://www.utahrealestate.com/search/map.search/type/2
  - Residential:  https://www.utahrealestate.com/search/map.search/type/1
  - Listing detail: https://www.utahrealestate.com/report/public.report/id/{listing_id}
"""

from __future__ import annotations

import asyncio
import logging
import re

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# UtahRealEstate.com type codes
URE_TYPE_MAP = {
    PropertyType.MULTI_FAMILY: "2",
    PropertyType.SINGLE_FAMILY: "1",
    PropertyType.ANY: "1",  # default to residential, filter later
}

BASE_URL = "https://www.utahrealestate.com"
SEARCH_URL = BASE_URL + "/search/map.search"
DETAIL_URL = BASE_URL + "/report/public.report/id"

REQUEST_DELAY = 2.0
MAX_RESULTS = 200


class UtahRealEstateBrowser(BaseScraper):
    """Scraper for UtahRealEstate.com using Playwright."""

    name = "utahrealestate"

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None

    async def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        return self._browser

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
        page = await context.new_page()
        listings: list[Listing] = []

        try:
            # Navigate to the search page with the correct property type
            type_code = URE_TYPE_MAP.get(criteria.property_type, "2")
            search_url = f"{SEARCH_URL}/type/{type_code}"
            logger.info(f"Navigating to {search_url}")

            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # Set search filters
            await self._set_filters(page, criteria)
            await page.wait_for_timeout(3000)

            # Extract listing results
            listings = await self._extract_listings(page, criteria)

            # Try to get detail data for each listing (if we got IDs)
            if listings:
                listings = await self._enrich_listings(context, listings)

        except Exception as e:
            logger.error(f"UtahRealEstate search failed: {e}")
        finally:
            await page.close()

        return listings

    async def _set_filters(self, page: Page, criteria: SearchCriteria) -> None:
        """Set city, price range, and other filters on the search page."""
        # Try to set location/city filter
        # The site typically has a location search box
        location_selectors = [
            'input[placeholder*="city"]',
            'input[placeholder*="location"]',
            'input[placeholder*="City"]',
            'input[placeholder*="Address"]',
            'input[name="location"]',
            'input[name="city"]',
            'input[id*="location"]',
            'input[id*="city"]',
            '#searchInput',
            '.search-input input',
            'input[type="text"]',
        ]

        for selector in location_selectors:
            try:
                el = await page.query_selector(selector)
                if el:
                    await el.click()
                    await el.fill("")
                    await el.type(f"{criteria.city}, {criteria.state}", delay=50)
                    await page.wait_for_timeout(1500)
                    # Try to click first autocomplete suggestion
                    suggestion_selectors = [
                        '.autocomplete-suggestion:first-child',
                        '.suggestion-item:first-child',
                        '[class*="suggestion"]:first-child',
                        '[class*="dropdown"] li:first-child',
                        '[class*="result"] li:first-child',
                    ]
                    for sug_sel in suggestion_selectors:
                        sug = await page.query_selector(sug_sel)
                        if sug:
                            await sug.click()
                            break
                    else:
                        await page.keyboard.press("Enter")
                    logger.info(f"Set location to {criteria.city}, {criteria.state}")
                    break
            except Exception:
                continue

        # Try to set price range
        await self._set_price_filter(page, "min", criteria.min_price)
        await self._set_price_filter(page, "max", criteria.max_price)

        # Click search button if available
        search_btn_selectors = [
            'button[type="submit"]',
            'button:has-text("Search")',
            'input[type="submit"]',
            'a:has-text("Search")',
            '#searchButton',
            '.search-btn',
        ]
        for selector in search_btn_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    break
            except Exception:
                continue

        await page.wait_for_timeout(2000)

    async def _set_price_filter(self, page: Page, minmax: str, value: int) -> None:
        """Set a min or max price filter."""
        if (minmax == "min" and value <= 0) or (minmax == "max" and value >= 999_999_999):
            return

        selectors = [
            f'input[name*="{minmax}price" i]',
            f'input[name*="{minmax}_price" i]',
            f'input[name*="price_{minmax}" i]',
            f'input[id*="{minmax}price" i]',
            f'input[id*="{minmax}Price" i]',
            f'input[placeholder*="{minmax}" i]',
        ]
        for selector in selectors:
            try:
                el = await page.query_selector(selector)
                if el:
                    await el.fill(str(value))
                    logger.info(f"Set {minmax} price to {value}")
                    return
            except Exception:
                continue

    async def _extract_listings(self, page: Page, criteria: SearchCriteria) -> list[Listing]:
        """Extract listing data from search results page."""
        listings: list[Listing] = []

        # Try multiple selector strategies for listing cards
        card_selectors = [
            '.listing-card',
            '.property-card',
            '.search-result-item',
            '[class*="listing"]',
            '[class*="property-card"]',
            'article',
            '.result-item',
            'div[data-listing-id]',
            'tr[data-listing-id]',
        ]

        cards = []
        for selector in card_selectors:
            cards = await page.query_selector_all(selector)
            if len(cards) > 1:  # Found listing cards
                logger.info(f"Found {len(cards)} cards with selector: {selector}")
                break

        for card in cards[:MAX_RESULTS]:
            listing = await self._parse_card(card, criteria)
            if listing:
                listings.append(listing)

        # If no cards found, try to extract from page content as text
        if not listings:
            logger.info("No listing cards found, trying text extraction")
            listings = await self._extract_from_text(page, criteria)

        return listings

    async def _parse_card(self, card, criteria: SearchCriteria) -> Listing | None:
        """Parse a single listing card element."""
        try:
            text = await card.inner_text()
            if not text.strip():
                return None

            # Extract price
            price = _extract_price(text)
            if not price:
                return None

            # Extract address
            address = ""
            addr_selectors = [
                '.address', '[class*="address"]', 'a[href*="report"]',
                '.street', '[class*="street"]',
            ]
            for sel in addr_selectors:
                el = await card.query_selector(sel)
                if el:
                    address = (await el.inner_text()).strip()
                    break
            if not address:
                # Try first line of text as address
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if lines:
                    address = lines[0]

            # Extract listing URL and ID
            listing_url = ""
            mls_number = None
            link = await card.query_selector('a[href*="report"], a[href*="listing"], a')
            if link:
                href = await link.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    href = BASE_URL + href
                listing_url = href
                id_match = re.search(r"/id/(\d+)", href)
                if id_match:
                    mls_number = id_match.group(1)

            # Extract beds/baths/sqft from text
            beds = _extract_number(text, r"(\d+)\s*(?:bed|br|bd)", default=None)
            baths = _extract_float(text, r"(\d+\.?\d*)\s*(?:bath|ba)")
            sqft = _extract_number(text, r"([\d,]+)\s*(?:sq\s*ft|sqft|sf)")

            # Extract units
            num_units = _extract_number(text, r"(\d+)\s*(?:unit|plex)")
            # Check for duplex/triplex/fourplex
            subtype = None
            text_lower = text.lower()
            if "fourplex" in text_lower or "4-plex" in text_lower:
                subtype = "Fourplex"
                num_units = num_units or 4
            elif "triplex" in text_lower or "3-plex" in text_lower:
                subtype = "Triplex"
                num_units = num_units or 3
            elif "duplex" in text_lower or "2-plex" in text_lower:
                subtype = "Duplex"
                num_units = num_units or 2

            return Listing(
                address=address,
                city=criteria.city,
                state=criteria.state,
                zip_code="",
                price=price,
                sqft=sqft,
                bedrooms=beds,
                bathrooms=baths,
                property_type="Multi-Family" if num_units and num_units > 1 else "",
                property_subtype=subtype,
                num_units=num_units,
                listing_url=listing_url,
                source="utahrealestate",
                mls_number=mls_number,
            )
        except Exception as e:
            logger.debug(f"Failed to parse card: {e}")
            return None

    async def _extract_from_text(self, page: Page, criteria: SearchCriteria) -> list[Listing]:
        """Last-resort extraction: parse the entire page text for listing patterns."""
        listings = []
        try:
            content = await page.content()
            # Look for links to listing detail pages
            detail_pattern = re.compile(r'/report/public\.report/id/(\d+)')
            ids = detail_pattern.findall(content)
            unique_ids = list(dict.fromkeys(ids))  # dedupe preserving order

            for listing_id in unique_ids[:MAX_RESULTS]:
                listings.append(Listing(
                    address="",
                    city=criteria.city,
                    state=criteria.state,
                    zip_code="",
                    price=0,
                    listing_url=f"{DETAIL_URL}/{listing_id}",
                    source="utahrealestate",
                    mls_number=listing_id,
                ))
        except Exception as e:
            logger.debug(f"Text extraction failed: {e}")
        return listings

    async def _enrich_listings(
        self, context: BrowserContext, listings: list[Listing]
    ) -> list[Listing]:
        """Visit individual listing pages to get detailed data."""
        enriched = []
        page = await context.new_page()

        for listing in listings:
            if not listing.listing_url:
                enriched.append(listing)
                continue

            try:
                await page.goto(listing.listing_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)

                text = await page.inner_text("body")

                # Try to fill in missing data from the detail page
                if not listing.price:
                    listing.price = _extract_price(text) or 0
                if not listing.sqft:
                    listing.sqft = _extract_number(text, r"([\d,]+)\s*(?:sq\s*ft|sqft|sf|total\s*sq)")
                if not listing.bedrooms:
                    listing.bedrooms = _extract_number(text, r"(\d+)\s*(?:bed|br|bd)")
                if not listing.bathrooms:
                    listing.bathrooms = _extract_float(text, r"(\d+\.?\d*)\s*(?:bath|ba)")
                if not listing.num_units:
                    listing.num_units = _extract_number(text, r"(\d+)\s*(?:unit|plex)")
                if not listing.year_built:
                    listing.year_built = _extract_number(text, r"(?:year\s*built|built)\s*:?\s*(\d{4})")
                if not listing.address:
                    # Try to get address from page title or heading
                    title = await page.title()
                    if title:
                        listing.address = title.split("|")[0].strip()

                # Look for income/rent data
                if not listing.gross_income:
                    income = _extract_money(text, r"(?:gross\s*income|rental\s*income|annual\s*income)\s*:?\s*\$?([\d,]+)")
                    if income:
                        listing.gross_income = float(income)
                if not listing.noi:
                    noi = _extract_money(text, r"(?:net\s*operating\s*income|noi)\s*:?\s*\$?([\d,]+)")
                    if noi:
                        listing.noi = float(noi)

                # Extract zip code
                if not listing.zip_code:
                    zip_match = re.search(r"\b(\d{5})\b", text[:2000])
                    if zip_match:
                        listing.zip_code = zip_match.group(1)

                enriched.append(listing)
                await asyncio.sleep(REQUEST_DELAY)

            except Exception as e:
                logger.debug(f"Failed to enrich listing {listing.mls_number}: {e}")
                enriched.append(listing)

        await page.close()
        return enriched

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


def _extract_price(text: str) -> int | None:
    """Extract a dollar price from text."""
    match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", text)
    if match:
        return int(match.group(1).replace(",", "").split(".")[0])
    return None


def _extract_number(text: str, pattern: str, default=None) -> int | None:
    """Extract a number using a regex pattern."""
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return default


def _extract_float(text: str, pattern: str) -> float | None:
    """Extract a float using a regex pattern."""
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _extract_money(text: str, pattern: str) -> int | None:
    """Extract a monetary value using a regex pattern."""
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return None
