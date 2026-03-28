"""Data models for real estate listings and search preferences."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Self

import csv
import io


class PropertyType(Enum):
    SINGLE_FAMILY = "single_family"
    MULTI_FAMILY = "multi_family"
    ANY = "any"

    def __str__(self) -> str:
        return self.value.replace("_", "-")


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
    days_on_market: int | None = None
    mls_number: str | None = None

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

    def to_dict(self) -> dict:
        d = asdict(self)
        d["price_per_sqft"] = self.price_per_sqft
        d["cap_rate"] = self.cap_rate
        d["grm"] = self.grm
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


def listings_to_csv(listings: list[Listing]) -> str:
    """Convert listings to a CSV string."""
    if not listings:
        return ""
    fieldnames = [
        "address", "city", "state", "zip_code", "price", "sqft",
        "bedrooms", "bathrooms", "property_type", "property_subtype",
        "num_units", "gross_income", "noi", "price_per_sqft", "cap_rate",
        "grm", "year_built", "days_on_market", "mls_number", "listing_url",
        "source",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for listing in listings:
        row = listing.to_dict()
        writer.writerow({k: row.get(k) for k in fieldnames})
    return output.getvalue()
