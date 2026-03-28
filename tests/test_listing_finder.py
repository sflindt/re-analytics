"""Tests for listing finder orchestrator."""

from unittest.mock import AsyncMock, patch

import pytest

from re_analytics.models import Listing, PropertyType, SearchCriteria
from re_analytics.listing_finder import find_listings, _deduplicate, _select_scrapers


def _make_listing(address="123 Main St", city="Ogden", source="test", **kw) -> Listing:
    defaults = dict(
        state="UT",
        zip_code="84401",
        price=350000,
        listing_url="https://example.com/1",
    )
    defaults.update(kw)
    return Listing(address=address, city=city, source=source, **defaults)


class TestDeduplicate:
    def test_removes_exact_duplicates(self):
        listings = [
            _make_listing(address="123 Main St", city="Ogden", source="a"),
            _make_listing(address="123 Main St", city="Ogden", source="b"),
        ]
        result = _deduplicate(listings)
        assert len(result) == 1

    def test_keeps_richer_listing(self):
        sparse = _make_listing(address="123 Main St", source="a")
        rich = _make_listing(address="123 Main St", source="b", sqft=1500, bedrooms=3)
        result = _deduplicate([sparse, rich])
        assert len(result) == 1
        assert result[0].sqft == 1500

    def test_different_addresses_kept(self):
        a = _make_listing(address="123 Main St")
        b = _make_listing(address="456 Oak Ave")
        result = _deduplicate([a, b])
        assert len(result) == 2

    def test_case_insensitive(self):
        a = _make_listing(address="123 MAIN ST", city="OGDEN")
        b = _make_listing(address="123 main st", city="ogden")
        result = _deduplicate([a, b])
        assert len(result) == 1


class TestSelectScrapers:
    def test_utah_with_no_api_key_uses_browser(self):
        criteria = SearchCriteria(city="Ogden", state="UT")
        scrapers = _select_scrapers(criteria)
        names = [s.name for s in scrapers]
        assert "utahrealestate" in names or "utahrealestate-api" in names
        assert "zillow" in names

    def test_non_utah_uses_zillow(self):
        criteria = SearchCriteria(city="Phoenix", state="AZ")
        scrapers = _select_scrapers(criteria)
        names = [s.name for s in scrapers]
        assert "zillow" in names
        assert "utahrealestate" not in names

    def test_explicit_source(self):
        criteria = SearchCriteria(city="Ogden", state="UT", sources=["zillow"])
        scrapers = _select_scrapers(criteria)
        assert len(scrapers) == 1
        assert scrapers[0].name == "zillow"
