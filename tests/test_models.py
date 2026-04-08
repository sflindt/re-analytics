"""Tests for data models and investment calculations."""

import pytest

from re_analytics.models import (
    InvestmentParams,
    Listing,
    PropertyType,
    SearchCriteria,
    calc_monthly_pmt,
    get_county_tax_rate,
    listings_to_csv,
)


def _make_listing(**kw) -> Listing:
    defaults = dict(
        address="123 Main St",
        city="Ogden",
        state="UT",
        zip_code="84401",
        price=350000,
        listing_url="https://example.com/1",
        source="test",
    )
    defaults.update(kw)
    return Listing(**defaults)


class TestListing:
    def test_price_per_sqft(self):
        l = _make_listing(price=300000, sqft=1500)
        assert l.price_per_sqft == 200.0

    def test_price_per_sqft_none_when_no_sqft(self):
        l = _make_listing(price=300000)
        assert l.price_per_sqft is None

    def test_cap_rate(self):
        l = _make_listing(price=500000, noi=40000.0)
        assert l.cap_rate == 8.0

    def test_cap_rate_none_when_no_noi(self):
        l = _make_listing(price=500000)
        assert l.cap_rate is None

    def test_grm(self):
        l = _make_listing(price=500000, gross_income=50000.0)
        assert l.grm == 10.0

    def test_grm_none_when_no_income(self):
        l = _make_listing(price=500000)
        assert l.grm is None

    def test_to_dict_includes_computed_fields(self):
        l = _make_listing(price=300000, sqft=1500, noi=24000.0, gross_income=30000.0)
        d = l.to_dict()
        assert d["price_per_sqft"] == 200.0
        assert d["cap_rate"] == 8.0
        assert d["grm"] == 10.0
        assert "monthly_pmt" in d
        assert "monthly_piti" in d
        assert "rent_multiplier" in d


class TestInvestmentCalcs:
    def test_rent_estimate(self):
        l = _make_listing(bedrooms=4)
        assert l.rent_estimate(600) == 4 * 600 * 12

    def test_rent_estimate_none_when_no_beds(self):
        l = _make_listing()
        assert l.rent_estimate() is None

    def test_rent_multiplier(self):
        l = _make_listing(price=300000, bedrooms=4)
        # 300000 / (4 * 600) = 125.0
        assert l.rent_multiplier(600) == 125.0

    def test_rent_multiplier_none_when_no_beds(self):
        l = _make_listing()
        assert l.rent_multiplier() is None

    def test_monthly_pmt_default_params(self):
        l = _make_listing(price=400000)
        pmt = l.monthly_pmt()
        # 75% LTV = $300k loan, 6.7% rate, 30yr
        assert 1900 < pmt < 2000

    def test_monthly_pmt_custom_params(self):
        params = InvestmentParams(down_pmt_pct=0.20, interest_rate=0.06)
        l = _make_listing(price=500000)
        pmt = l.monthly_pmt(params)
        # 80% LTV = $400k loan, 6% rate
        assert 2300 < pmt < 2500

    def test_monthly_piti_includes_taxes_and_insurance(self):
        l = _make_listing(price=400000, city="Ogden", state="UT")
        piti = l.monthly_piti()
        pmt = l.monthly_pmt()
        # PITI should be greater than PMT (adds taxes + insurance)
        assert piti > pmt
        assert piti - pmt > 100  # taxes + insurance should be meaningful

    def test_required_down_payment(self):
        l = _make_listing(price=400000)
        assert l.required_down_payment(0.25) == 100000

    def test_calc_monthly_pmt_zero_rate(self):
        result = calc_monthly_pmt(360000, 0.0, 360)
        assert result == 1000.0

    def test_calc_monthly_pmt_zero_loan(self):
        result = calc_monthly_pmt(0, 0.067, 360)
        assert result == 0.0


class TestCountyTaxRate:
    def test_known_utah_city(self):
        rate = get_county_tax_rate("Salt Lake City", "UT")
        assert rate == 0.0059

    def test_ogden(self):
        rate = get_county_tax_rate("Ogden", "UT")
        assert rate == 0.0063

    def test_unknown_city_returns_default(self):
        rate = get_county_tax_rate("Nowheresville", "UT")
        assert rate == 0.0055

    def test_non_utah_returns_default(self):
        rate = get_county_tax_rate("Phoenix", "AZ")
        assert rate == 0.0055

    def test_case_insensitive(self):
        rate = get_county_tax_rate("SALT LAKE CITY", "UT")
        assert rate == 0.0059


class TestSearchCriteria:
    def test_defaults(self):
        c = SearchCriteria(city="Ogden")
        assert c.state == "UT"
        assert c.property_type == PropertyType.MULTI_FAMILY
        assert c.search_area_sqmi == 50

    def test_property_type_str(self):
        assert str(PropertyType.MULTI_FAMILY) == "multi-family"


class TestListingsToCsv:
    def test_empty_list(self):
        assert listings_to_csv([]) == ""

    def test_csv_has_headers(self):
        csv_str = listings_to_csv([_make_listing()])
        lines = csv_str.strip().split("\n")
        headers = lines[0].split(",")
        assert "price" in headers
        assert "monthly_piti" in headers
        assert "rent_multiplier" in headers

    def test_csv_includes_investment_fields(self):
        csv_str = listings_to_csv([_make_listing(price=400000, bedrooms=4, sqft=2000)])
        lines = csv_str.strip().split("\n")
        headers = lines[0].split(",")
        data = lines[1].split(",")
        row = dict(zip(headers, data))
        assert float(row["price_per_sqft"]) == 200.0
        assert float(row["monthly_pmt"]) > 0
        assert float(row["monthly_piti"]) > 0
        assert float(row["rent_multiplier"]) > 0
