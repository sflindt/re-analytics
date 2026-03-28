"""Tests for data models."""

from re_analytics.models import Listing, PropertyType, SearchCriteria, listings_to_csv


def _make_listing(**overrides) -> Listing:
    defaults = dict(
        address="123 Main St",
        city="Ogden",
        state="UT",
        zip_code="84401",
        price=350000,
        listing_url="https://example.com/listing/1",
        source="test",
    )
    defaults.update(overrides)
    return Listing(**defaults)


class TestListing:
    def test_price_per_sqft(self):
        listing = _make_listing(price=300000, sqft=1500)
        assert listing.price_per_sqft == 200.0

    def test_price_per_sqft_none_when_no_sqft(self):
        listing = _make_listing(price=300000, sqft=None)
        assert listing.price_per_sqft is None

    def test_cap_rate(self):
        listing = _make_listing(price=300000, noi=24000.0)
        assert listing.cap_rate == 8.0

    def test_cap_rate_none_when_no_noi(self):
        listing = _make_listing(price=300000, noi=None)
        assert listing.cap_rate is None

    def test_grm(self):
        listing = _make_listing(price=300000, gross_income=36000.0)
        assert listing.grm == 8.33

    def test_grm_none_when_no_income(self):
        listing = _make_listing(price=300000, gross_income=None)
        assert listing.grm is None

    def test_to_dict_includes_computed_fields(self):
        listing = _make_listing(price=300000, sqft=1500, noi=24000.0, gross_income=36000.0)
        d = listing.to_dict()
        assert d["price_per_sqft"] == 200.0
        assert d["cap_rate"] == 8.0
        assert d["grm"] == 8.33


class TestSearchCriteria:
    def test_defaults(self):
        c = SearchCriteria(city="Ogden")
        assert c.state == "UT"
        assert c.min_price == 0
        assert c.max_price == 999_999_999
        assert c.property_type == PropertyType.MULTI_FAMILY

    def test_property_type_str(self):
        assert str(PropertyType.MULTI_FAMILY) == "multi-family"
        assert str(PropertyType.SINGLE_FAMILY) == "single-family"


class TestListingsToCsv:
    def test_empty_list(self):
        assert listings_to_csv([]) == ""

    def test_csv_has_headers(self):
        listings = [_make_listing()]
        csv = listings_to_csv(listings)
        lines = csv.strip().split("\n")
        assert "address" in lines[0]
        assert "price" in lines[0]
        assert len(lines) == 2  # header + 1 data row

    def test_csv_includes_computed_fields(self):
        listings = [_make_listing(price=300000, sqft=1500)]
        csv = listings_to_csv(listings)
        assert "price_per_sqft" in csv
        assert "200.0" in csv
