"""UtahRealEstate.com scraper using Playwright headless browser.

The site is a JS-rendered SPA that blocks direct HTTP requests.
We use Playwright to automate a real browser session.

Known URL patterns:
  Search:  https://www.utahrealestate.com/search/map.search/type/{type_code}
  Detail:  https://www.utahrealestate.com/{listno}  (short URL)
  Legacy:  https://www.utahrealestate.com/report/public.single.report/report/detailed/listno/{mls}

Query params (appended to search URL):
  city, listprice1, listprice2, tot_bed1, tot_bath1, tot_sqf1, page, etc.

Type codes: 1=Residential, 2=Multi-family, 3=Land, 5=Commercial, 7=Rental

Known DOM selectors (may change — scraper uses multiple fallback strategies):
  Listing cards: table.public-detail-quickview
  MLS number:    p.public-detail-overview-b
  Stats line:    p.public-detail-overview (beds/baths/sqft as text)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlencode

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Response

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# UtahRealEstate.com type codes
URE_TYPE_MAP = {
    PropertyType.MULTI_FAMILY: "2",
    PropertyType.SINGLE_FAMILY: "1",
    PropertyType.ANY: "1",
}

BASE_URL = "https://www.utahrealestate.com"
SEARCH_PATH = "/search/map.search"

REQUEST_DELAY = 2.0
MAX_PAGES = 10
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

    def _build_search_url(self, criteria: SearchCriteria, page_num: int = 1) -> str:
        """Build search URL with query parameters."""
        type_code = URE_TYPE_MAP.get(criteria.property_type, "2")
        url = f"{BASE_URL}{SEARCH_PATH}/type/{type_code}"

        params: dict[str, str] = {}
        params["city"] = criteria.city
        if criteria.min_price > 0:
            params["listprice1"] = str(criteria.min_price)
        if criteria.max_price < 999_999_999:
            params["listprice2"] = str(criteria.max_price)
        if page_num > 1:
            params["page"] = str(page_num)

        if params:
            url += "?" + urlencode(params)
        return url

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
        all_listings: list[Listing] = []
        ajax_listings: list[dict] = []

        # Intercept AJAX responses that may contain JSON listing data
        async def on_response(response: Response) -> None:
            try:
                url = response.url
                if response.status == 200 and ("search" in url or "listing" in url):
                    content_type = response.headers.get("content-type", "")
                    if "json" in content_type or "javascript" in content_type:
                        try:
                            data = await response.json()
                            if isinstance(data, dict):
                                # Look for listing arrays in the response
                                for key in ("listings", "results", "data", "properties"):
                                    if key in data and isinstance(data[key], list):
                                        ajax_listings.extend(data[key])
                                        break
                                # Some responses have listings at the top level
                                if "listno" in data or "ListingKey" in data:
                                    ajax_listings.append(data)
                            elif isinstance(data, list) and data:
                                ajax_listings.extend(data)
                        except Exception:
                            pass
            except Exception:
                pass

        page.on("response", on_response)

        try:
            for page_num in range(1, MAX_PAGES + 1):
                url = self._build_search_url(criteria, page_num)
                logger.info(f"Fetching page {page_num}: {url}")

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(4000)

                # On first page, also try submitting the search form with filters
                if page_num == 1:
                    await self._try_set_filters(page, criteria)
                    await page.wait_for_timeout(3000)

                # Extract listings from the DOM
                page_listings = await self._extract_listings(page, criteria)

                if not page_listings and not ajax_listings:
                    logger.info(f"No listings on page {page_num}, stopping pagination.")
                    break

                all_listings.extend(page_listings)

                if len(all_listings) >= MAX_RESULTS:
                    break

                await asyncio.sleep(REQUEST_DELAY)

            # If AJAX interception caught JSON data, parse those too
            if ajax_listings:
                logger.info(f"AJAX interception caught {len(ajax_listings)} items")
                for item in ajax_listings:
                    listing = self._parse_ajax_item(item, criteria)
                    if listing:
                        all_listings.append(listing)

            # Deduplicate by MLS number
            seen = set()
            deduped = []
            for l in all_listings:
                key = l.mls_number or l.address
                if key and key not in seen:
                    seen.add(key)
                    deduped.append(l)
            all_listings = deduped[:MAX_RESULTS]

            # Enrich listings with detail page data (for listings missing key fields)
            needs_enrichment = [l for l in all_listings if not l.sqft or not l.price]
            if needs_enrichment and len(needs_enrichment) <= 50:
                all_listings = await self._enrich_listings(context, all_listings)

        except Exception as e:
            logger.error(f"UtahRealEstate search failed: {e}")
        finally:
            await page.close()

        return all_listings

    async def _try_set_filters(self, page: Page, criteria: SearchCriteria) -> None:
        """Try to interact with the search form to set filters."""
        # Try known input names for the UtahRealEstate search form
        filter_map = {
            "city": criteria.city,
            "listprice1": str(criteria.min_price) if criteria.min_price > 0 else "",
            "listprice2": str(criteria.max_price) if criteria.max_price < 999_999_999 else "",
        }
        for name, value in filter_map.items():
            if not value:
                continue
            for selector in [
                f'input[name="{name}"]',
                f'input[id="{name}"]',
                f'select[name="{name}"]',
            ]:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        tag = await el.evaluate("e => e.tagName.toLowerCase()")
                        if tag == "select":
                            await el.select_option(value=value)
                        else:
                            await el.fill(value)
                        break
                except Exception:
                    continue

        # Also try generic location input
        location_selectors = [
            'input[placeholder*="city" i]',
            'input[placeholder*="location" i]',
            'input[placeholder*="address" i]',
            '#searchInput',
        ]
        for selector in location_selectors:
            try:
                el = await page.query_selector(selector)
                if el:
                    await el.fill("")
                    await el.type(f"{criteria.city}, {criteria.state}", delay=50)
                    await page.wait_for_timeout(1500)
                    # Click first autocomplete suggestion
                    for sug_sel in [
                        '.autocomplete-suggestion:first-child',
                        '[class*="suggestion"]:first-child',
                        '[class*="dropdown"] li:first-child',
                    ]:
                        sug = await page.query_selector(sug_sel)
                        if sug:
                            await sug.click()
                            break
                    else:
                        await page.keyboard.press("Enter")
                    break
            except Exception:
                continue

        # Click search/submit button
        for selector in [
            'button[type="submit"]',
            'button:has-text("Search")',
            'input[type="submit"]',
            '#searchButton',
        ]:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    break
            except Exception:
                continue

    async def _extract_listings(self, page: Page, criteria: SearchCriteria) -> list[Listing]:
        """Extract listing data from search results page."""
        listings: list[Listing] = []

        # Known and fallback selectors for listing cards
        card_selectors = [
            'table.public-detail-quickview',       # Known from house-hunter scraper
            '.listing-card',
            '.property-card',
            '.search-result-item',
            'article[class*="listing"]',
            '[data-listing-id]',
            'div[class*="property-card"]',
            '.result-item',
            'article',
        ]

        cards = []
        for selector in card_selectors:
            cards = await page.query_selector_all(selector)
            if len(cards) >= 1:
                logger.info(f"Found {len(cards)} cards with selector: {selector}")
                break

        for card in cards[:MAX_RESULTS]:
            listing = await self._parse_card(card, criteria)
            if listing:
                listings.append(listing)

        # Fallback: extract listing IDs from page HTML
        if not listings:
            logger.info("No listing cards found, trying link/ID extraction")
            listings = await self._extract_from_html(page, criteria)

        return listings

    async def _parse_card(self, card, criteria: SearchCriteria) -> Listing | None:
        """Parse a single listing card element."""
        try:
            text = await card.inner_text()
            if not text.strip():
                return None

            price = _extract_price(text)
            if not price:
                return None

            # Address — try known selectors then fallback to first text line
            address = ""
            for sel in [
                '.address', '[class*="address"]', 'h2 i', 'h2 span',
                'a[href*="report"]', '.street', '[class*="street"]',
            ]:
                el = await card.query_selector(sel)
                if el:
                    address = (await el.inner_text()).strip()
                    break
            if not address:
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if lines:
                    address = lines[0]

            # MLS number — try known selector then regex
            mls_number = None
            for sel in ['p.public-detail-overview-b', '[class*="mls"]', '[class*="listno"]']:
                el = await card.query_selector(sel)
                if el:
                    mls_text = await el.inner_text()
                    mls_match = re.search(r"(\d{5,})", mls_text)
                    if mls_match:
                        mls_number = mls_match.group(1)
                    break
            if not mls_number:
                mls_match = re.search(r"(?:MLS|#|listno)\s*:?\s*(\d{5,})", text, re.IGNORECASE)
                if mls_match:
                    mls_number = mls_match.group(1)

            # Listing URL
            listing_url = ""
            link = await card.query_selector('a[href]')
            if link:
                href = await link.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    href = BASE_URL + href
                listing_url = href
                # Try to extract MLS from URL
                if not mls_number:
                    id_match = re.search(r"/(\d{5,})", href)
                    if id_match:
                        mls_number = id_match.group(1)

            # If we have MLS but no URL, build the short URL
            if mls_number and not listing_url:
                listing_url = f"{BASE_URL}/{mls_number}"

            # Stats — try known selector then regex
            beds, baths, sqft = None, None, None
            stats_el = await card.query_selector('p.public-detail-overview')
            stats_text = (await stats_el.inner_text()) if stats_el else text
            beds = _extract_number(stats_text, r"(\d+)\s*(?:bed|br|bd)")
            baths = _extract_float(stats_text, r"(\d+\.?\d*)\s*(?:bath|ba)")
            sqft = _extract_number(stats_text, r"([\d,]+)\s*(?:sq\s*ft|sqft|sf)")

            # Units / property subtype
            num_units = _extract_number(text, r"(\d+)\s*(?:unit|plex)")
            subtype = _detect_subtype(text)
            if subtype and not num_units:
                num_units = {"Duplex": 2, "Triplex": 3, "Fourplex": 4}.get(subtype)

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

    def _parse_ajax_item(self, item: dict, criteria: SearchCriteria) -> Listing | None:
        """Parse a listing from intercepted AJAX JSON."""
        try:
            listno = str(item.get("listno", item.get("ListingKey", item.get("id", ""))))
            if not listno:
                return None

            price = item.get("listprice", item.get("ListPrice", item.get("price", 0)))
            address = item.get("address", item.get("street", ""))
            city = item.get("city", item.get("City", criteria.city))
            state = item.get("state", item.get("StateOrProvince", criteria.state))
            zip_code = str(item.get("zip", item.get("PostalCode", "")))

            return Listing(
                address=str(address),
                city=str(city),
                state=str(state),
                zip_code=zip_code,
                price=int(float(price)) if price else 0,
                sqft=_safe_int(item.get("sqft", item.get("LivingArea"))),
                bedrooms=_safe_int(item.get("beds", item.get("BedroomsTotal"))),
                bathrooms=_safe_float(item.get("baths", item.get("BathroomsTotalInteger"))),
                property_type=str(item.get("proptype", item.get("PropertyType", ""))),
                property_subtype=item.get("PropertySubType"),
                num_units=_safe_int(item.get("units", item.get("NumberOfUnitsTotal"))),
                gross_income=_safe_float(item.get("gross_income", item.get("GrossIncome"))),
                noi=_safe_float(item.get("noi", item.get("NetOperatingIncome"))),
                year_built=_safe_int(item.get("yearblt", item.get("YearBuilt"))),
                days_on_market=_safe_int(item.get("dom", item.get("DaysOnMarket"))),
                listing_url=f"{BASE_URL}/{listno}",
                source="utahrealestate",
                mls_number=listno,
            )
        except Exception as e:
            logger.debug(f"Failed to parse AJAX item: {e}")
            return None

    async def _extract_from_html(self, page: Page, criteria: SearchCriteria) -> list[Listing]:
        """Fallback: extract listing IDs from page HTML and build stub listings."""
        listings = []
        try:
            content = await page.content()
            # Match various listing ID patterns
            patterns = [
                re.compile(r'/report/public\.(?:single\.)?report/(?:report/detailed/)?listno/(\d+)'),
                re.compile(r'/report/public\.report/id/(\d+)'),
                re.compile(r'href="[^"]*?/(\d{6,8})"'),  # Short URLs like /2131834
            ]
            all_ids = []
            for pattern in patterns:
                all_ids.extend(pattern.findall(content))

            unique_ids = list(dict.fromkeys(all_ids))
            for listing_id in unique_ids[:MAX_RESULTS]:
                listings.append(Listing(
                    address="",
                    city=criteria.city,
                    state=criteria.state,
                    zip_code="",
                    price=0,
                    listing_url=f"{BASE_URL}/{listing_id}",
                    source="utahrealestate",
                    mls_number=listing_id,
                ))
        except Exception as e:
            logger.debug(f"HTML extraction failed: {e}")
        return listings

    async def _enrich_listings(
        self, context: BrowserContext, listings: list[Listing]
    ) -> list[Listing]:
        """Visit individual listing detail pages to fill in missing data."""
        enriched = []
        page = await context.new_page()

        for listing in listings:
            if not listing.listing_url:
                enriched.append(listing)
                continue

            # Skip enrichment for listings that already have key data
            if listing.price and listing.sqft and listing.bedrooms:
                enriched.append(listing)
                continue

            try:
                await page.goto(listing.listing_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)

                text = await page.inner_text("body")

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
                if not listing.property_subtype:
                    listing.property_subtype = _detect_subtype(text)
                if not listing.year_built:
                    listing.year_built = _extract_number(text, r"(?:year\s*built|built)\s*:?\s*(\d{4})")
                if not listing.address:
                    title = await page.title()
                    if title:
                        listing.address = title.split("|")[0].strip()

                # Income data
                if not listing.gross_income:
                    income = _extract_money(text, r"(?:gross\s*income|rental\s*income|annual\s*income)\s*:?\s*\$?([\d,]+)")
                    if income:
                        listing.gross_income = float(income)
                if not listing.noi:
                    noi = _extract_money(text, r"(?:net\s*operating\s*income|noi)\s*:?\s*\$?([\d,]+)")
                    if noi:
                        listing.noi = float(noi)

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


# --- Utility functions ---

def _detect_subtype(text: str) -> str | None:
    """Detect property subtype from text."""
    t = text.lower()
    if "fourplex" in t or "4-plex" in t or "4 plex" in t:
        return "Fourplex"
    if "triplex" in t or "3-plex" in t or "3 plex" in t:
        return "Triplex"
    if "duplex" in t or "2-plex" in t or "2 plex" in t:
        return "Duplex"
    return None


def _extract_price(text: str) -> int | None:
    match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", text)
    if match:
        return int(match.group(1).replace(",", "").split(".")[0])
    return None


def _extract_number(text: str, pattern: str, default=None) -> int | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return default


def _extract_float(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _extract_money(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
