"""Interest rate data: current mortgage rates, Fed funds rate, and projections."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

FRED_SERIES = {
    "mortgage_30yr": "MORTGAGE30US",      # 30-Year Fixed Rate Mortgage Average
    "mortgage_15yr": "MORTGAGE15US",      # 15-Year Fixed Rate Mortgage Average
    "fed_funds": "FEDFUNDS",             # Federal Funds Effective Rate
    "treasury_10yr": "GS10",             # 10-Year Treasury Constant Maturity
}

FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"


@dataclass
class RateSnapshot:
    """Current interest rate environment."""

    mortgage_30yr: float | None = None
    mortgage_15yr: float | None = None
    fed_funds_rate: float | None = None
    treasury_10yr: float | None = None
    as_of: str | None = None

    @property
    def spread_over_treasury(self) -> float | None:
        """Mortgage rate spread over 10-year Treasury."""
        if self.mortgage_30yr and self.treasury_10yr:
            return round(self.mortgage_30yr - self.treasury_10yr, 2)
        return None

    @property
    def rate_direction(self) -> str:
        """Simple assessment of rate environment."""
        if self.fed_funds_rate is None:
            return "Unknown"
        if self.fed_funds_rate >= 5.0:
            return "Restrictive"
        elif self.fed_funds_rate >= 3.5:
            return "Moderately Restrictive"
        elif self.fed_funds_rate >= 2.0:
            return "Neutral"
        else:
            return "Accommodative"

    def monthly_payment_comparison(self, loan_amount: float) -> dict[str, float]:
        """Show monthly P&I at different rate scenarios."""
        scenarios = {}
        base = self.mortgage_30yr or 6.5

        for label, rate in [
            ("Current", base),
            ("-0.5%", base - 0.5),
            ("-1.0%", base - 1.0),
            ("+0.5%", base + 0.5),
        ]:
            monthly_rate = rate / 100 / 12
            n = 360
            if monthly_rate > 0:
                pmt = loan_amount * (monthly_rate * (1 + monthly_rate) ** n) / (
                    (1 + monthly_rate) ** n - 1
                )
            else:
                pmt = loan_amount / n
            scenarios[f"{label} ({rate:.1f}%)"] = round(pmt, 0)

        return scenarios


async def fetch_rates_fred(api_key: str | None = None) -> RateSnapshot:
    """Fetch latest rates from FRED (Federal Reserve Economic Data).

    Works without API key using the public observations endpoint.
    With a key, more reliable and higher rate limits.
    """
    snapshot = RateSnapshot()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for field_name, series_id in FRED_SERIES.items():
            try:
                params = {
                    "series_id": series_id,
                    "sort_order": "desc",
                    "limit": "5",
                    "file_type": "json",
                }
                if api_key:
                    params["api_key"] = api_key

                resp = await client.get(FRED_API_URL, params=params)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                observations = data.get("observations", [])
                for obs in observations:
                    val = obs.get("value", ".")
                    if val != ".":
                        setattr(snapshot, field_name, float(val))
                        if not snapshot.as_of:
                            snapshot.as_of = obs.get("date")
                        break

            except Exception as e:
                logger.debug(f"FRED fetch failed for {series_id}: {e}")

    return snapshot


def get_fallback_rates() -> RateSnapshot:
    """Fallback rates when FRED API is unavailable."""
    return RateSnapshot(
        mortgage_30yr=6.65,
        mortgage_15yr=5.89,
        fed_funds_rate=4.33,
        treasury_10yr=4.25,
        as_of=datetime.now().strftime("%Y-%m-%d"),
    )


async def fetch_appreciation_fred(api_key: str | None = None) -> dict:
    """Fetch FHFA House Price Index for SLC metro from FRED.

    Series: ATNHPIUS41620Q  (All-Transactions HPI, SLC MSA, quarterly)
    Returns dict with 'yoy_pct' and 'five_yr_pct' appreciation.
    """
    series_id = "ATNHPIUS41620Q"
    result: dict[str, float | None] = {"yoy_pct": None, "five_yr_pct": None, "values": []}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            params = {
                "series_id": series_id,
                "sort_order": "desc",
                "limit": "25",  # ~6 years of quarterly data
                "file_type": "json",
            }
            if api_key:
                params["api_key"] = api_key

            resp = await client.get(FRED_API_URL, params=params)
            if resp.status_code != 200:
                return result

            data = resp.json()
            observations = data.get("observations", [])
            values = []
            for obs in observations:
                val = obs.get("value", ".")
                if val != ".":
                    values.append({"date": obs.get("date"), "value": float(val)})

            if len(values) >= 5:
                # YoY: latest vs ~4 quarters ago
                latest = values[0]["value"]
                yoy_ref = values[4]["value"]  # ~1 year back
                if yoy_ref > 0:
                    result["yoy_pct"] = round((latest - yoy_ref) / yoy_ref * 100, 1)

            if len(values) >= 21:
                # 5-year: latest vs ~20 quarters ago
                latest = values[0]["value"]
                fiveyr_ref = values[20]["value"]
                if fiveyr_ref > 0:
                    result["five_yr_pct"] = round((latest - fiveyr_ref) / fiveyr_ref * 100, 1)

            result["values"] = values[:8]  # Keep last 2 years for display

    except Exception as e:
        logger.debug(f"FHFA HPI fetch failed: {e}")

    return result


async def get_current_rates(fred_api_key: str | None = None) -> RateSnapshot:
    """Get current rates, falling back to defaults if API fails."""
    try:
        snapshot = await fetch_rates_fred(fred_api_key)
        if snapshot.mortgage_30yr is not None:
            return snapshot
    except Exception as e:
        logger.debug(f"Rate fetch failed: {e}")

    return get_fallback_rates()
