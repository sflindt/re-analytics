"""Zillow listing scraper via Scrapfly API (bypasses PerimeterX).

Routes Zillow search requests through Scrapfly's anti-bot proxy.
Falls back to direct pyzill if no SCRAPFLY_API_KEY is configured.

Uses bounding-box search via geopy, matching the user's proven notebook approach.
Extracts all fields needed for investment analysis.
"""

from __future__ import annotations

import json
import logging
import os
import re

from scrapfly import ScrapflyClient, ScrapeConfig
from geopy import Point
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

ZILLOW_SEARCH_URL = "https://www.zillow.com/async-create-search-page-state"

# Pre-computed city centers for major cities
CITY_COORDS: dict[str, tuple[float, float]] = {
    # Salt Lake County
    "salt lake city": (40.7608, -111.8910),
    "millcreek": (40.6825, -111.8468),
    "sandy": (40.5649, -111.8590),
    "west jordan": (40.6097, -111.9391),
    "south jordan": (40.5622, -111.9297),
    "taylorsville": (40.6677, -111.9388),
    "murray": (40.6669, -111.8879),
    "holladay": (40.6688, -111.8246),
    "midvale": (40.6111, -111.8999),
    "cottonwood heights": (40.6199, -111.8102),
    "riverton": (40.5219, -111.9391),
    "south salt lake": (40.7188, -111.8883),
    "west valley city": (40.6916, -111.9741),
    "herriman": (40.5143, -111.9922),
    "draper": (40.5246, -111.8638),
    # Utah County
    "provo": (40.2338, -111.6585),
    "orem": (40.2969, -111.6946),
    "lehi": (40.3916, -111.8508),
    "spanish fork": (40.1149, -111.6549),
    "springville": (40.1652, -111.6107),
    "american fork": (40.3769, -111.7953),
    "saratoga springs": (40.3492, -111.9046),
    "pleasant grove": (40.3641, -111.7385),
    "payson": (40.0444, -111.7322),
    "eagle mountain": (40.3141, -112.0069),
    "highland": (40.4258, -111.7953),
    "alpine": (40.4533, -111.7778),
    "vineyard": (40.3050, -111.7475),
    "lindon": (40.3427, -111.7214),
    # Weber County
    "ogden": (41.2230, -111.9738),
    "roy": (41.1616, -112.0263),
    "north ogden": (41.3072, -111.9602),
    # Davis County
    "layton": (41.0602, -111.9711),
    "bountiful": (40.8894, -111.8808),
    "kaysville": (41.0352, -111.9386),
    "clearfield": (41.1105, -112.0261),
    "syracuse": (41.0894, -112.0647),
    "farmington": (40.9805, -111.8872),
    # Washington County
    "st george": (37.0965, -113.5684),
    # Cache County
    "logan": (41.7370, -111.8338),
    # Iron County
    "cedar city": (37.6775, -113.0619),
    # Summit County
    "park city": (40.6461, -111.4980),
    # Out-of-state major metros
    "phoenix": (33.4484, -112.0740),
    "seattle": (47.6062, -122.3321),
    "denver": (39.7392, -104.9903),
    "las vegas": (36.1699, -115.1398),
    "boise": (43.6150, -116.2023),
    "austin": (30.2672, -97.7431),
    "dallas": (32.7767, -96.7970),
    "houston": (29.7604, -95.3698),
    "san antonio": (29.4241, -98.4936),
    "portland": (45.5152, -122.6784),
    "tucson": (32.2226, -110.9747),
    "albuquerque": (35.0844, -106.6504),
    "colorado springs": (38.8339, -104.8214),
    "reno": (39.5296, -119.8138),
    "mesa": (33.4152, -111.8315),
    "atlanta": (33.7490, -84.3880),
    "nashville": (36.1627, -86.7816),
    "charlotte": (35.2271, -80.8431),
    "tampa": (27.9506, -82.4572),
    "orlando": (28.5383, -81.3792),
    "jacksonville": (30.3322, -81.6557),
    "indianapolis": (39.7684, -86.1581),
    "columbus": (39.9612, -82.9988),
    "kansas city": (39.0997, -94.5786),
}

# pyzill filter states for property types
MULTI_FAMILY_FILTER = {
    "sortSelection": {"value": "globalrelevanceex"},
    "isAllHomes": {"value": True},
    "isMultiFamily": {"value": True},
    "isSingleFamily": {"value": False},
    "isCondo": {"value": False},
    "isTownhouse": {"value": False},
    "isLotLand": {"value": False},
    "isManufactured": {"value": False},
}

SINGLE_FAMILY_FILTER = {
    "sortSelection": {"value": "globalrelevanceex"},
    "isAllHomes": {"value": True},
    "isMultiFamily": {"value": False},
    "isSingleFamily": {"value": True},
    "isCondo": {"value": False},
    "isTownhouse": {"value": False},
    "isLotLand": {"value": False},
    "isManufactured": {"value": False},
}

ALL_HOMES_FILTER = {
    "sortSelection": {"value": "globalrelevanceex"},
    "isAllHomes": {"value": True},
}

FILTER_MAP = {
    PropertyType.MULTI_FAMILY: MULTI_FAMILY_FILTER,
    PropertyType.SINGLE_FAMILY: SINGLE_FAMILY_FILTER,
    PropertyType.ANY: ALL_HOMES_FILTER,
}

MAX_PAGES = 5


def _get_bounding_box(
    center_lat: float, center_long: float, target_area_sqmi: int
) -> tuple[float, float, float, float]:
    """Compute bounding box (ne_lat, ne_long, sw_lat, sw_long) from center + area."""
    half_side = (target_area_sqmi ** 0.5) / 2
    north = geodesic(miles=half_side).destination(Point(center_lat, center_long), 0)
    south = geodesic(miles=half_side).destination(Point(center_lat, center_long), 180)
    east = geodesic(miles=half_side).destination(Point(center_lat, center_long), 90)
    west = geodesic(miles=half_side).destination(Point(center_lat, center_long), 270)
    return north.latitude, east.longitude, south.latitude, west.longitude


def _geocode_city(city: str, state: str) -> tuple[float, float] | None:
    """Geocode a city using Nominatim. Returns (lat, lon) or None."""
    try:
        geolocator = Nominatim(user_agent="re-analytics")
        location = geolocator.geocode(f"{city}, {state}, USA")
        if location:
            return (location.latitude, location.longitude)
    except Exception as e:
        logger.warning(f"Geocoding failed for {city}, {state}: {e}")
    return None


def _build_zillow_body(
    search_value: str,
    ne_lat: float,
    ne_long: float,
    sw_lat: float,
    sw_long: float,
    filter_state: dict,
    page: int,
    min_price: int | None = None,
    max_price: int | None = None,
) -> dict:
    """Build the JSON body for Zillow's async-create-search-page-state endpoint."""
    fs = filter_state.copy()
    if min_price is not None:
        fs["price"] = {"min": min_price}
    if max_price is not None:
        fs.setdefault("price", {})["max"] = max_price

    return {
        "searchQueryState": {
            "isMapVisible": True,
            "isListVisible": True,
            "mapBounds": {
                "north": ne_lat,
                "east": ne_long,
                "south": sw_lat,
                "west": sw_long,
            },
            "filterState": fs,
            "mapZoom": 1,
            "pagination": {"currentPage": page},
            "usersSearchTerm": search_value,
        },
        "wants": {
            "cat1": ["listResults", "mapResults"],
            "cat2": ["total"],
        },
        "requestId": page,
        "isDebugRequest": False,
    }


class ZillowScraper(BaseScraper):
    """Scraper using Scrapfly API to bypass Zillow's PerimeterX protection."""

    name = "zillow"

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._api_key = os.environ.get("SCRAPFLY_API_KEY", "")
        self._scrapfly: ScrapflyClient | None = None
        if self._api_key:
            self._scrapfly = ScrapflyClient(key=self._api_key)

    async def _scrapfly_zillow_search(self, body: dict) -> dict:
        """Send a Zillow search request via Scrapfly SDK."""
        config = ScrapeConfig(
            url=ZILLOW_SEARCH_URL,
            method="PUT",
            body=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Origin": "https://www.zillow.com",
            },
            asp=True,
            country="us",
            render_js=False,
        )
        result = await self._scrapfly.async_scrape(config)
        content = result.scrape_result["content"]
        if not content:
            raise ValueError("Scrapfly returned empty content")
        logger.debug(f"Scrapfly response length: {len(content)} chars")
        return json.loads(content)

    async def search(self, criteria: SearchCriteria) -> list[Listing]:
        city_key = criteria.city.strip().lower()
        coords = CITY_COORDS.get(city_key)

        if not coords:
            coords = _geocode_city(criteria.city, criteria.state)
            if not coords:
                logger.warning(
                    f"Could not find coordinates for {criteria.city}, {criteria.state}"
                )
                return []

        ne_lat, ne_long, sw_lat, sw_long = _get_bounding_box(
            coords[0], coords[1], criteria.search_area_sqmi
        )

        filter_state = FILTER_MAP.get(criteria.property_type, ALL_HOMES_FILTER).copy()

        min_price_arg = criteria.min_price if criteria.min_price > 0 else None
        max_price_arg = criteria.max_price if criteria.max_price < 999_999_999 else None

        search_value = f"{criteria.city}, {criteria.state}"
        unique_zpids: set[str] = set()
        all_listings: list[Listing] = []

        use_scrapfly = bool(self._api_key)
        if not use_scrapfly:
            logger.warning(
                "SCRAPFLY_API_KEY not set — falling back to direct pyzill. "
                "This will likely fail due to PerimeterX bot detection. "
                "Sign up for a free key at https://scrapfly.io"
            )

        for page in range(1, MAX_PAGES + 1):
            if self.debug:
                logger.debug(
                    f"Zillow search page {page}: {search_value} "
                    f"bbox=({ne_lat:.4f},{ne_long:.4f},{sw_lat:.4f},{sw_long:.4f}) "
                    f"via={'scrapfly' if use_scrapfly else 'pyzill'}"
                )

            try:
                if use_scrapfly:
                    body = _build_zillow_body(
                        search_value=search_value,
                        ne_lat=ne_lat,
                        ne_long=ne_long,
                        sw_lat=sw_lat,
                        sw_long=sw_long,
                        filter_state=filter_state,
                        page=page,
                        min_price=min_price_arg,
                        max_price=max_price_arg,
                    )
                    data = await self._scrapfly_zillow_search(body)
                else:
                    from pyzill.search import search as pyzill_search
                    data = pyzill_search(
                        pagination=page,
                        search_value=search_value,
                        min_beds=None,
                        max_beds=None,
                        min_bathrooms=None,
                        max_bathrooms=None,
                        min_price=min_price_arg,
                        max_price=max_price_arg,
                        ne_lat=ne_lat,
                        ne_long=ne_long,
                        sw_lat=sw_lat,
                        sw_long=sw_long,
                        zoom_value=1,
                        filter_state=filter_state,
                    )
            except Exception as e:
                err_msg = str(e)
                if "Expecting value" in err_msg or "JSONDecodeError" in type(e).__name__:
                    logger.warning(
                        f"Zillow returned empty/non-JSON response on page {page}. "
                        "This usually means rate limiting or bot detection. "
                        "Try again in a few minutes."
                    )
                elif "402" in err_msg or "Payment Required" in err_msg:
                    logger.warning(
                        "Scrapfly API credits exhausted. "
                        "Sign up at https://scrapfly.io for more credits."
                    )
                else:
                    logger.warning(f"Zillow search failed on page {page}: {e}")
                break

            # Log top-level keys for debugging response structure
            logger.debug(f"Zillow response keys: {list(data.keys())}")

            # Zillow nests results under cat1.searchResults or at top level
            # Try nested structure first (cat1.searchResults.mapResults)
            search_results = (
                data.get("cat1", {}).get("searchResults", {})
                or data.get("searchResults", {})
                or data
            )
            if page == 1:
                results = search_results.get("mapResults", []) or search_results.get("listResults", [])
            else:
                results = search_results.get("listResults", [])

            # Fallback: try top-level keys
            if not results:
                results = data.get("mapResults", []) or data.get("listResults", [])

            logger.debug(f"Zillow page {page}: {len(results)} results")

            if not results:
                break

            initial_count = len(all_listings)
            parse_failures = 0
            for idx, item in enumerate(results):
                if idx == 0:
                    logger.debug(f"Sample result keys: {list(item.keys())}")
                    logger.debug(f"Sample result: {json.dumps(item, default=str)[:500]}")
                zpid = str(item.get("zpid", ""))
                if not zpid or zpid in unique_zpids:
                    continue
                unique_zpids.add(zpid)
                listing = self._parse_result(item, criteria)
                if listing:
                    all_listings.append(listing)
                else:
                    parse_failures += 1
            logger.debug(f"Page {page}: parsed {len(all_listings) - initial_count} listings, {parse_failures} parse failures")

            # Stop if no new unique listings found
            if len(all_listings) == initial_count:
                if self.debug:
                    logger.debug(f"No new unique listings on page {page}, stopping")
                break

        return all_listings

    def _parse_result(self, item: dict, criteria: SearchCriteria) -> Listing | None:
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

            # Days on Zillow (comes as milliseconds)
            days_on_zillow = None
            time_on_zillow = home_info.get("timeOnZillow")
            if time_on_zillow and isinstance(time_on_zillow, (int, float)):
                days_on_zillow = time_on_zillow / (1000 * 60 * 60 * 24)

            if not address and not price:
                return None

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
            )
        except Exception as e:
            logger.warning(f"Failed to parse Zillow result: {e} | keys={list(item.keys())}")
            return None

    async def close(self) -> None:
        if self._scrapfly:
            self._scrapfly.close()
