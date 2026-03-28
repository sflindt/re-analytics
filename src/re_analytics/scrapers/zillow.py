"""Zillow listing scraper using pyzill (curl_cffi for Chrome impersonation).

Uses bounding-box search via geopy, matching the user's proven notebook approach.
Extracts all fields needed for investment analysis.
"""

from __future__ import annotations

import logging
import re

from geopy import Point
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

import pyzill

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Pre-computed city centers for major Utah cities (from user's notebook + additions)
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


class ZillowScraper(BaseScraper):
    """Scraper using pyzill (curl_cffi Chrome impersonation)."""

    name = "zillow"

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

        if criteria.min_price > 0:
            filter_state["price"] = filter_state.get("price", {})
            filter_state["price"]["min"] = criteria.min_price
        if criteria.max_price < 999_999_999:
            filter_state["price"] = filter_state.get("price", {})
            filter_state["price"]["max"] = criteria.max_price

        search_value = f"{criteria.city}, {criteria.state}"
        unique_zpids: set[str] = set()
        all_listings: list[Listing] = []

        for page in range(1, MAX_PAGES + 1):
            if self.debug:
                logger.debug(
                    f"Zillow pyzill search page {page}: {search_value} "
                    f"bbox=({ne_lat:.4f},{ne_long:.4f},{sw_lat:.4f},{sw_long:.4f})"
                )

            try:
                data = pyzill.search(
                    pagination=page,
                    search_value=search_value,
                    min_beds=0,
                    max_beds=0,
                    min_bathrooms=0,
                    max_bathrooms=0,
                    min_price=criteria.min_price,
                    max_price=criteria.max_price if criteria.max_price < 999_999_999 else 0,
                    ne_lat=ne_lat,
                    ne_long=ne_long,
                    sw_lat=sw_lat,
                    sw_long=sw_long,
                    zoom_value=8,
                    filter_state=filter_state,
                )
            except Exception as e:
                logger.warning(f"pyzill search failed on page {page}: {e}")
                break

            # Use mapResults (all listings up to 500) on first page,
            # listResults for pagination
            results = data.get("mapResults", []) if page == 1 else data.get("listResults", [])

            if self.debug:
                logger.debug(f"Zillow page {page}: {len(results)} results")

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
            logger.debug(f"Failed to parse Zillow result: {e}")
            return None

    async def close(self) -> None:
        pass
