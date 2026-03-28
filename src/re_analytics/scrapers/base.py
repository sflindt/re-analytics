"""Abstract base class for all listing scrapers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from re_analytics.models import Listing, SearchCriteria


class BaseScraper(ABC):
    """Base interface for real estate listing scrapers."""

    name: str = "base"

    @abstractmethod
    async def search(self, criteria: SearchCriteria) -> list[Listing]:
        """Search for listings matching the given criteria.

        Returns a list of Listing objects.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources (browser contexts, HTTP clients, etc.)."""
        ...
