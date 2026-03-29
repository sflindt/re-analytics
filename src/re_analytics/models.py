"""Data models for real estate listings and search preferences."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum

import csv
import io
import math


class PropertyType(Enum):
    SINGLE_FAMILY = "single_family"
    MULTI_FAMILY = "multi_family"
    ANY = "any"

    def __str__(self) -> str:
        return self.value.replace("_", "-")


# Utah county tax lookup tables (from user's notebook)
CITY_TO_COUNTY: dict[str, str] = {
    # Utah County
    "provo": "Utah County", "orem": "Utah County", "lehi": "Utah County",
    "spanish fork": "Utah County", "springville": "Utah County",
    "american fork": "Utah County", "saratoga springs": "Utah County",
    "pleasant grove": "Utah County", "payson": "Utah County",
    "mapleton": "Utah County", "highland": "Utah County",
    "eagle mountain": "Utah County", "cedar hills": "Utah County",
    "lindon": "Utah County", "salem": "Utah County", "santaquin": "Utah County",
    "vineyard": "Utah County", "alpine": "Utah County",
    "elk ridge": "Utah County", "woodland hills": "Utah County",
    "bluffdale": "Utah County", "draper": "Utah County",
    # Salt Lake County
    "salt lake city": "Salt Lake County", "millcreek": "Salt Lake County",
    "sandy": "Salt Lake County", "west jordan": "Salt Lake County",
    "south jordan": "Salt Lake County", "taylorsville": "Salt Lake County",
    "murray": "Salt Lake County", "holladay": "Salt Lake County",
    "midvale": "Salt Lake County", "cottonwood heights": "Salt Lake County",
    "riverton": "Salt Lake County", "south salt lake": "Salt Lake County",
    "west valley city": "Salt Lake County", "magna": "Salt Lake County",
    "kearns": "Salt Lake County", "herriman": "Salt Lake County",
    "brighton": "Salt Lake County", "emigration canyon": "Salt Lake County",
    "copperton": "Salt Lake County", "alta": "Salt Lake County",
    # Weber County
    "ogden": "Weber County", "roy": "Weber County", "north ogden": "Weber County",
    "south ogden": "Weber County", "riverdale": "Weber County",
    "washington terrace": "Weber County", "pleasant view": "Weber County",
    # Davis County
    "layton": "Davis County", "bountiful": "Davis County",
    "kaysville": "Davis County", "clearfield": "Davis County",
    "syracuse": "Davis County", "farmington": "Davis County",
    "centerville": "Davis County", "north salt lake": "Davis County",
    "woods cross": "Davis County", "west point": "Davis County",
    # Washington County
    "st george": "Washington County", "washington": "Washington County",
    "hurricane": "Washington County", "ivins": "Washington County",
    "santa clara": "Washington County", "leeds": "Washington County",
    # Cache County
    "logan": "Cache County", "north logan": "Cache County",
    "smithfield": "Cache County", "hyde park": "Cache County",
    # Iron County
    "cedar city": "Iron County",
    # Summit County
    "park city": "Summit County",
    # Tooele County
    "tooele": "Tooele County",
}

COUNTY_TAX_RATES: dict[str, float] = {
    "Beaver County": 0.0042, "Box Elder County": 0.0054, "Cache County": 0.0051,
    "Carbon County": 0.0072, "Daggett County": 0.0045, "Davis County": 0.0057,
    "Duchesne County": 0.0066, "Emery County": 0.0061, "Garfield County": 0.0040,
    "Grand County": 0.0040, "Iron County": 0.0046, "Juab County": 0.0049,
    "Kane County": 0.0045, "Millard County": 0.0052, "Morgan County": 0.0053,
    "Piute County": 0.0049, "Rich County": 0.0034, "San Juan County": 0.0084,
    "Sanpete County": 0.0054, "Sevier County": 0.0059, "Summit County": 0.0034,
    "Tooele County": 0.0060, "Uintah County": 0.0055, "Utah County": 0.0045,
    "Wasatch County": 0.0048, "Washington County": 0.0046, "Wayne County": 0.0038,
    "Weber County": 0.0063, "Salt Lake County": 0.0059,
}

DEFAULT_TAX_RATE = 0.0055


def get_county_tax_rate(city: str, state: str = "UT") -> float:
    """Look up county tax rate for a Utah city. Returns default for non-Utah or unknown."""
    if state.upper() != "UT":
        return DEFAULT_TAX_RATE
    county = CITY_TO_COUNTY.get(city.strip().lower())
    if county:
        return COUNTY_TAX_RATES.get(county, DEFAULT_TAX_RATE)
    return DEFAULT_TAX_RATE


@dataclass
class InvestmentParams:
    """Configurable investment assumptions for property analysis."""
    per_bed_rent: float = 600.0
    down_pmt_pct: float = 0.25
    interest_rate: float = 0.067
    insurance_rate: float = 0.0043
    loan_term_months: int = 360


def calc_monthly_pmt(loan_amount: float, annual_rate: float, term_months: int = 360) -> float:
    """Standard mortgage PMT formula."""
    if loan_amount <= 0:
        return 0.0
    monthly_rate = annual_rate / 12
    if monthly_rate == 0:
        return loan_amount / term_months
    return loan_amount * (monthly_rate * (1 + monthly_rate) ** term_months) / (
        (1 + monthly_rate) ** term_months - 1
    )


@dataclass
class Listing:
    """A single real estate listing."""

    address: str
    city: str
    state: str
    zip_code: str
    price: int
    listing_url: str
    source: str

    sqft: int | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    property_type: str = ""
    property_subtype: str | None = None
    num_units: int | None = None
    gross_income: float | None = None
    noi: float | None = None
    year_built: int | None = None
    days_on_market: float | None = None
    mls_number: str | None = None

    # Fields from Zillow / pyzill
    zpid: str | None = None
    zestimate: int | None = None
    rent_zestimate: int | None = None
    tax_assessed_value: int | None = None
    latitude: float | None = None
    longitude: float | None = None

    # Agent analytics fields
    status: str | None = None  # Active, Sold, Pending, etc.
    original_list_price: int | None = None
    sold_price: int | None = None
    sale_date: str | None = None  # ISO 8601
    list_date: str | None = None  # ISO 8601
    lot_size: float | None = None  # sqft
    hoa_fees: float | None = None  # monthly

    @property
    def sale_to_list_ratio(self) -> float | None:
        """Sold price / original list price. < 1.0 means sold below ask."""
        sell = self.sold_price or 0
        ask = self.original_list_price or self.price or 0
        if sell > 0 and ask > 0:
            return round(sell / ask, 3)
        return None

    @property
    def price_reduction(self) -> int | None:
        """Original list price minus current price (positive = price dropped)."""
        if self.original_list_price and self.price:
            diff = self.original_list_price - self.price
            return diff if diff != 0 else None
        return None

    @property
    def price_per_sqft(self) -> float | None:
        if self.price and self.sqft:
            return round(self.price / self.sqft, 2)
        return None

    @property
    def cap_rate(self) -> float | None:
        if self.noi and self.price:
            return round(self.noi / self.price * 100, 2)
        return None

    @property
    def grm(self) -> float | None:
        """Gross rent multiplier."""
        if self.gross_income and self.price and self.gross_income > 0:
            return round(self.price / self.gross_income, 2)
        return None

    def rent_estimate(self, per_bed_rent: float = 600.0) -> float | None:
        """Estimated annual rent = beds * per_bed_rent * 12."""
        beds = self.bedrooms or 0
        if beds <= 0:
            return None
        return beds * per_bed_rent * 12

    def rent_multiplier(self, per_bed_rent: float = 600.0) -> float | None:
        """Price / (beds * per_bed_rent). Lower = better deal."""
        beds = self.bedrooms or 0
        if beds <= 0 or not self.price:
            return None
        return round(self.price / (beds * per_bed_rent), 1)

    def monthly_taxes(self) -> float:
        """Monthly property tax based on city/county lookup."""
        rate = get_county_tax_rate(self.city, self.state)
        return rate * self.price / 12

    def monthly_insurance(self, insurance_rate: float = 0.0043) -> float:
        """Monthly insurance estimate."""
        return insurance_rate * self.price / 12

    def monthly_pmt(self, params: InvestmentParams | None = None) -> float:
        """Monthly mortgage payment (P&I only)."""
        p = params or InvestmentParams()
        loan_amount = self.price * (1 - p.down_pmt_pct)
        return calc_monthly_pmt(loan_amount, p.interest_rate, p.loan_term_months)

    def monthly_piti(self, params: InvestmentParams | None = None) -> float:
        """Monthly PITI = mortgage + taxes + insurance."""
        p = params or InvestmentParams()
        return (
            self.monthly_pmt(p)
            + self.monthly_taxes()
            + self.monthly_insurance(p.insurance_rate)
        )

    def required_down_payment(self, pct: float = 0.25) -> float:
        return self.price * pct

    def to_dict(self, params: InvestmentParams | None = None) -> dict:
        p = params or InvestmentParams()
        d = asdict(self)
        d["price_per_sqft"] = self.price_per_sqft
        d["cap_rate"] = self.cap_rate
        d["grm"] = self.grm
        d["sale_to_list_ratio"] = self.sale_to_list_ratio
        d["price_reduction"] = self.price_reduction
        d["rent_estimate"] = self.rent_estimate(p.per_bed_rent)
        d["rent_multiplier"] = self.rent_multiplier(p.per_bed_rent)
        d["monthly_taxes"] = round(self.monthly_taxes(), 2)
        d["monthly_insurance"] = round(self.monthly_insurance(p.insurance_rate), 2)
        d["monthly_pmt"] = round(self.monthly_pmt(p), 2)
        d["monthly_piti"] = round(self.monthly_piti(p), 2)
        d["required_down_payment"] = round(self.required_down_payment(p.down_pmt_pct), 2)
        return d


@dataclass
class SearchCriteria:
    """User search parameters for listings."""

    city: str
    state: str = "UT"
    min_price: int = 0
    max_price: int = 999_999_999
    property_type: PropertyType = PropertyType.MULTI_FAMILY
    sources: list[str] = field(default_factory=list)
    search_area_sqmi: int = 50


def listings_to_csv(listings: list[Listing], params: InvestmentParams | None = None) -> str:
    """Convert listings to CSV matching the user's sale_data format."""
    if not listings:
        return ""
    p = params or InvestmentParams()
    fieldnames = [
        "zpid", "status", "property_type", "address", "zip_code", "city", "state",
        "latitude", "longitude", "listing_url", "price", "original_list_price",
        "sold_price", "sale_to_list_ratio", "price_reduction",
        "bedrooms", "bathrooms", "sqft", "lot_size", "year_built",
        "zestimate", "rent_zestimate", "tax_assessed_value", "hoa_fees",
        "days_on_market", "list_date", "sale_date", "num_units", "gross_income", "noi",
        "price_per_sqft", "rent_multiplier", "monthly_taxes", "monthly_insurance",
        "monthly_pmt", "monthly_piti", "cap_rate", "grm",
        "rent_estimate", "required_down_payment",
        "source", "mls_number",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for listing in listings:
        row = listing.to_dict(p)
        writer.writerow({k: row.get(k) for k in fieldnames})
    return output.getvalue()
