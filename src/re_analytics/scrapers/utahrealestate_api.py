"""UtahRealEstate.com RESO Web API client.

Requires a bearer token from https://vendor.utahrealestate.com/
Set RESO_API_TOKEN in your .env file.
"""

from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.scrapers.base import BaseScraper

load_dotenv()

BASE_URL = "https://resoapi.utahrealestate.com/reso/odata"
PAGE_SIZE = 200

# Map our PropertyType enum to RESO PropertyType values
PROPERTY_TYPE_MAP = {
    PropertyType.MULTI_FAMILY: "Residential Income",
    PropertyType.SINGLE_FAMILY: "Residential",
    PropertyType.ANY: None,
}

SELECT_FIELDS = [
    "ListingKey",
    "ListPrice",
    "LivingArea",
    "BedroomsTotal",
    "BathroomsTotalInteger",
    "NumberOfUnitsTotal",
    "PropertyType",
    "PropertySubType",
    "GrossIncome",
    "NetOperatingIncome",
    "YearBuilt",
    "DaysOnMarket",
    "StandardStatus",
    "StreetNumber",
    "StreetName",
    "StreetSuffix",
    "City",
    "StateOrProvince",
    "PostalCode",
    "ListingId",
]


class UtahRealEstateAPI(BaseScraper):
    """Scraper using the official RESO Web API."""

    name = "utahrealestate-api"

    def __init__(self) -> None:
        self.token = os.getenv("RESO_API_TOKEN", "")
        self.client: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.token != "your_bearer_token_here")

    def _get_client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
                timeout=30.0,
            )
        return self.client

    def _build_filter(self, criteria: SearchCriteria) -> str:
        filters = []
        filters.append("StandardStatus eq 'Active'")
        filters.append(f"City eq '{criteria.city}'")
        filters.append(f"ListPrice ge {criteria.min_price}")
        filters.append(f"ListPrice le {criteria.max_price}")

        reso_type = PROPERTY_TYPE_MAP.get(criteria.property_type)
        if reso_type:
            filters.append(f"PropertyType eq '{reso_type}'")

        return " and ".join(filters)

    async def search(self, criteria: SearchCriteria) -> list[Listing]:
        if not self.is_configured:
            return []

        client = self._get_client()
        listings: list[Listing] = []
        skip = 0

        while True:
            params = {
                "$filter": self._build_filter(criteria),
                "$select": ",".join(SELECT_FIELDS),
                "$top": str(PAGE_SIZE),
                "$skip": str(skip),
                "$orderby": "ListPrice asc",
            }

            resp = await client.get("/Property", params=params)
            if resp.status_code != 200:
                break

            data = resp.json()
            results = data.get("value", [])
            if not results:
                break

            for item in results:
                listing = self._parse_listing(item)
                if listing:
                    listings.append(listing)

            if len(results) < PAGE_SIZE:
                break
            skip += PAGE_SIZE

        return listings

    def _parse_listing(self, item: dict) -> Listing | None:
        street_num = item.get("StreetNumber", "")
        street_name = item.get("StreetName", "")
        street_suffix = item.get("StreetSuffix", "")
        address = f"{street_num} {street_name} {street_suffix}".strip()

        listing_id = item.get("ListingId", item.get("ListingKey", ""))
        listing_url = f"https://www.utahrealestate.com/report/public.report/id/{listing_id}"

        return Listing(
            address=address,
            city=item.get("City", ""),
            state=item.get("StateOrProvince", "UT"),
            zip_code=item.get("PostalCode", ""),
            price=int(item.get("ListPrice", 0)),
            sqft=_int_or_none(item.get("LivingArea")),
            bedrooms=_int_or_none(item.get("BedroomsTotal")),
            bathrooms=_float_or_none(item.get("BathroomsTotalInteger")),
            property_type=item.get("PropertyType", ""),
            property_subtype=item.get("PropertySubType"),
            num_units=_int_or_none(item.get("NumberOfUnitsTotal")),
            gross_income=_float_or_none(item.get("GrossIncome")),
            noi=_float_or_none(item.get("NetOperatingIncome")),
            year_built=_int_or_none(item.get("YearBuilt")),
            days_on_market=_int_or_none(item.get("DaysOnMarket")),
            listing_url=listing_url,
            source=self.name,
            mls_number=str(listing_id) if listing_id else None,
        )

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None


def _int_or_none(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
