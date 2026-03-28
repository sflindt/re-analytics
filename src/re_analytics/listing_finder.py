"""Orchestrates listing scrapers based on search criteria."""

from __future__ import annotations

import logging

from rich.console import Console

from re_analytics.models import Listing, SearchCriteria
from re_analytics.scrapers.base import BaseScraper
from re_analytics.scrapers.utahrealestate_api import UtahRealEstateAPI
from re_analytics.scrapers.utahrealestate_browser import UtahRealEstateBrowser
from re_analytics.scrapers.zillow import ZillowScraper

logger = logging.getLogger(__name__)
console = Console()

# States served by UtahRealEstate.com
UTAH_STATES = {"UT"}


async def find_listings(criteria: SearchCriteria, debug: bool = False) -> list[Listing]:
    """Find listings matching criteria using the best available scrapers.

    For Utah: tries RESO API first, then UtahRealEstate browser scraper.
    For all states: uses Zillow as a universal fallback.
    If specific sources are requested in criteria.sources, only those are used.
    """
    scrapers: list[BaseScraper] = _select_scrapers(criteria)
    all_listings: list[Listing] = []

    for scraper in scrapers:
        try:
            if debug:
                scraper.debug = True
            console.print(f"  Searching [cyan]{scraper.name}[/cyan]...", end=" ")
            results = await scraper.search(criteria)
            console.print(f"found [green]{len(results)}[/green] listings")
            all_listings.extend(results)
        except Exception as e:
            console.print(f"[red]failed[/red]: {e}")
            logger.exception(f"Scraper {scraper.name} failed")
        finally:
            await scraper.close()

    # Deduplicate by address (normalized)
    return _deduplicate(all_listings)


def _select_scrapers(criteria: SearchCriteria) -> list[BaseScraper]:
    """Pick the right scrapers based on state and user preferences."""
    if criteria.sources:
        # User explicitly requested specific sources
        source_map = {
            "utahrealestate-api": UtahRealEstateAPI,
            "utahrealestate": UtahRealEstateBrowser,
            "zillow": ZillowScraper,
        }
        return [source_map[s]() for s in criteria.sources if s in source_map]

    scrapers: list[BaseScraper] = []

    if criteria.state.upper() in UTAH_STATES:
        # Try API first (faster, richer data), then browser fallback
        api = UtahRealEstateAPI()
        if api.is_configured:
            scrapers.append(api)
        else:
            scrapers.append(UtahRealEstateBrowser())

    # Zillow as universal fallback / additional source
    scrapers.append(ZillowScraper())

    return scrapers


def _deduplicate(listings: list[Listing]) -> list[Listing]:
    """Remove duplicate listings by normalized address."""
    seen: dict[str, Listing] = {}
    for listing in listings:
        key = _normalize_address(listing.address, listing.city, listing.state)
        if key not in seen:
            seen[key] = listing
        else:
            # Keep the one with more data (more non-None fields)
            existing = seen[key]
            if _data_richness(listing) > _data_richness(existing):
                seen[key] = listing
    return list(seen.values())


def _normalize_address(address: str, city: str, state: str) -> str:
    """Normalize an address for deduplication."""
    return f"{address.lower().strip()},{city.lower().strip()},{state.lower().strip()}"


def _data_richness(listing: Listing) -> int:
    """Count non-None optional fields as a measure of data quality."""
    fields = [
        listing.sqft, listing.bedrooms, listing.bathrooms,
        listing.num_units, listing.gross_income, listing.noi,
        listing.year_built, listing.days_on_market, listing.mls_number,
    ]
    return sum(1 for f in fields if f is not None)
