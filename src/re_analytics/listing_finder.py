"""Orchestrates listing scrapers based on search criteria."""

from __future__ import annotations

import logging
import os

from rich.console import Console

from re_analytics.cache import get_cached, set_cached
from re_analytics.models import InvestmentParams, Listing, SearchCriteria
from re_analytics.scrapers.base import BaseScraper
from re_analytics.scrapers.utahrealestate_api import UtahRealEstateAPI
from re_analytics.scrapers.utahrealestate_web import UtahRealEstateWebScraper
from re_analytics.scrapers.redfin import RedfinScraper
from re_analytics.scrapers.zillow import ZillowScraper
from re_analytics.scrapers.zillow_browser import ZillowBrowserScraper
from re_analytics.scrapers.rentcast import RentcastEnricher

logger = logging.getLogger(__name__)
console = Console()

# States served by UtahRealEstate.com
UTAH_STATES = {"UT"}


async def find_listings(
    criteria: SearchCriteria, debug: bool = False, use_cache: bool = True, cache_ttl: int = 3600
) -> list[Listing]:
    """Find listings matching criteria using the best available scrapers.

    Source routing:
      Utah: UtahRealEstate API (if configured) → UtahRealEstate Web → Zillow → Redfin
      Other: Zillow → Redfin

    If specific sources are requested in criteria.sources, only those are used.
    """
    if use_cache:
        cached = get_cached(criteria, ttl=cache_ttl)
        if cached is not None:
            console.print(f"  [dim]Using cached results ({len(cached)} listings)[/dim]")
            return cached

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
    deduped = _deduplicate(all_listings)

    # Optionally enrich with rent estimates
    enricher = RentcastEnricher()
    if enricher.is_configured and deduped:
        try:
            console.print(f"  Enriching with [cyan]rentcast[/cyan] rent estimates...", end=" ")
            deduped = await enricher.enrich_listings(deduped)
            console.print("[green]done[/green]")
        except Exception as e:
            console.print(f"[red]failed[/red]: {e}")
        finally:
            await enricher.close()

    if use_cache and deduped:
        set_cached(criteria, deduped)

    return deduped


def _select_scrapers(criteria: SearchCriteria) -> list[BaseScraper]:
    """Pick the right scrapers based on state and user preferences."""
    if criteria.sources:
        source_map: dict[str, type[BaseScraper]] = {
            "utahrealestate-api": UtahRealEstateAPI,
            "utahrealestate-web": UtahRealEstateWebScraper,
            "redfin": RedfinScraper,
            "zillow": ZillowScraper,
            "zillow-browser": ZillowBrowserScraper,
        }
        return [source_map[s]() for s in criteria.sources if s in source_map]

    scrapers: list[BaseScraper] = []

    if criteria.state.upper() in UTAH_STATES:
        # RESO API is best if configured
        api = UtahRealEstateAPI()
        if api.is_configured:
            scrapers.append(api)

        # Web scraper as fallback for Utah
        scrapers.append(UtahRealEstateWebScraper())

    # Zillow: prefer Playwright (free/unlimited), fall back to Scrapfly if configured
    scrapfly_key = os.environ.get("SCRAPFLY_API_KEY", "")
    if scrapfly_key:
        scrapers.append(ZillowScraper())
    else:
        scrapers.append(ZillowBrowserScraper())

    # Redfin as additional source
    scrapers.append(RedfinScraper())

    return scrapers


def _deduplicate(listings: list[Listing]) -> list[Listing]:
    """Remove duplicate listings by normalized address."""
    seen: dict[str, Listing] = {}
    for listing in listings:
        key = _normalize_address(listing.address, listing.city, listing.state)
        if key not in seen:
            seen[key] = listing
        else:
            existing = seen[key]
            if _data_richness(listing) > _data_richness(existing):
                seen[key] = listing
    return list(seen.values())


def _normalize_address(address: str, city: str, state: str) -> str:
    return f"{address.lower().strip()},{city.lower().strip()},{state.lower().strip()}"


def _data_richness(listing: Listing) -> int:
    fields = [
        listing.sqft, listing.bedrooms, listing.bathrooms,
        listing.num_units, listing.gross_income, listing.noi,
        listing.year_built, listing.days_on_market, listing.mls_number,
        listing.zestimate, listing.rent_zestimate, listing.tax_assessed_value,
        listing.latitude, listing.longitude,
    ]
    return sum(1 for f in fields if f is not None)
