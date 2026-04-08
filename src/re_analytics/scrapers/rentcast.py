"""Rentcast API for rent estimate enrichment.

Provides rent estimates for properties that don't have income data.
Free tier: 50 calls/month at rentcast.io/api.

Set RENTCAST_API_KEY in your .env file.
"""

from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv

from re_analytics.models import Listing

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rentcast.io/v1"


class RentcastEnricher:
    """Enrich listings with rent estimates from Rentcast API."""

    def __init__(self) -> None:
        self.api_key = os.getenv("RENTCAST_API_KEY", "")
        self._client: httpx.AsyncClient | None = None
        self._calls_made = 0

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key != "your_api_key_here")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={
                    "X-Api-Key": self.api_key,
                    "Accept": "application/json",
                },
                timeout=15.0,
            )
        return self._client

    async def estimate_rent(self, listing: Listing) -> float | None:
        """Get a rent estimate for a listing. Returns annual gross income estimate."""
        if not self.is_configured:
            return None

        client = self._get_client()

        params: dict[str, str] = {}
        if listing.address and listing.city and listing.state:
            params["address"] = f"{listing.address}, {listing.city}, {listing.state}"
        elif listing.zip_code:
            params["zipCode"] = listing.zip_code
        else:
            return None

        if listing.bedrooms:
            params["bedrooms"] = str(listing.bedrooms)
        if listing.bathrooms:
            params["bathrooms"] = str(listing.bathrooms)
        if listing.sqft:
            params["squareFootage"] = str(listing.sqft)
        if listing.property_type:
            params["propertyType"] = listing.property_type

        try:
            resp = await client.get("/avm/rent/long-term", params=params)
            self._calls_made += 1

            if resp.status_code != 200:
                logger.debug(f"Rentcast returned {resp.status_code} for {listing.address}")
                return None

            data = resp.json()
            monthly_rent = data.get("rent") or data.get("rentRangeLow")
            if monthly_rent:
                # Multiply by units if multi-family, else assume 1 unit
                units = listing.num_units or 1
                annual = float(monthly_rent) * 12 * units
                return annual

        except Exception as e:
            logger.debug(f"Rentcast API failed for {listing.address}: {e}")

        return None

    async def enrich_listings(
        self, listings: list[Listing], max_calls: int = 10
    ) -> list[Listing]:
        """Enrich listings that are missing income data with rent estimates.

        Only enriches up to max_calls listings to conserve API quota.
        """
        if not self.is_configured:
            logger.info("Rentcast not configured, skipping enrichment")
            return listings

        enriched = 0
        for listing in listings:
            if listing.gross_income is not None:
                continue
            if enriched >= max_calls:
                break

            rent = await self.estimate_rent(listing)
            if rent:
                listing.gross_income = rent
                enriched += 1
                logger.debug(f"Enriched {listing.address}: est. income ${rent:,.0f}/yr")

        if enriched:
            logger.info(f"Enriched {enriched} listings with rent estimates ({self._calls_made} API calls)")

        return listings

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
