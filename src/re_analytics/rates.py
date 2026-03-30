"""Interest rate data: current mortgage rates, Fed funds rate, and projections."""

from __future__ import annotations

import logging
import os
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


# CBSA codes for major US metros — maps lowercase city name to CBSA code.
# FHFA HPI series pattern: ATNHPIUS{CBSA}Q
METRO_CBSA = {
    "new york": "35620", "los angeles": "31080", "chicago": "16980",
    "dallas": "19100", "houston": "26420", "washington": "47900",
    "philadelphia": "37980", "miami": "33100", "atlanta": "12060",
    "boston": "14460", "phoenix": "38060", "san francisco": "41860",
    "riverside": "40140", "detroit": "19820", "seattle": "42660",
    "minneapolis": "33460", "san diego": "41740", "tampa": "45300",
    "denver": "19740", "st. louis": "41180", "baltimore": "12580",
    "orlando": "36740", "charlotte": "16740", "san antonio": "41700",
    "portland": "38900", "sacramento": "40900", "pittsburgh": "38300",
    "austin": "12420", "las vegas": "29820", "cincinnati": "17140",
    "kansas city": "28140", "columbus": "18140", "indianapolis": "26900",
    "cleveland": "17460", "san jose": "41940", "nashville": "34980",
    "virginia beach": "47260", "jacksonville": "27260",
    "providence": "39300", "milwaukee": "33340", "oklahoma city": "36420",
    "raleigh": "39580", "memphis": "32820", "richmond": "40060",
    "louisville": "31140", "new orleans": "35380", "salt lake city": "41620",
    "hartford": "25540", "birmingham": "13820", "buffalo": "15380",
    "rochester": "40380", "tucson": "46060", "tulsa": "46140",
    "fresno": "23420", "omaha": "36540", "boise": "14260",
    "provo": "39340", "ogden": "36260", "st. george": "41100",
    "logan": "30860",
    # Common short names
    "nyc": "35620", "la": "31080", "sf": "41860", "dc": "47900",
    "slc": "41620", "phx": "38060", "atl": "12060",
}


def _get_cbsa(city: str, state: str) -> str | None:
    """Look up CBSA code for a city. Tries exact match, then fuzzy prefix."""
    key = city.lower().strip()
    if key in METRO_CBSA:
        return METRO_CBSA[key]
    # Try prefix match (e.g. "Salt Lake" matches "salt lake city")
    for metro, cbsa in METRO_CBSA.items():
        if metro.startswith(key) or key.startswith(metro):
            return cbsa
    return None


async def fetch_appreciation_fred(api_key: str | None = None, city: str = "", state: str = "") -> dict:
    """Fetch FHFA House Price Index for a metro from FRED.

    Dynamically looks up the CBSA code for the given city/state.
    Falls back to national index (USSTHPI) if no metro match found.
    """
    cbsa = _get_cbsa(city, state) if city else None
    if cbsa:
        series_id = f"ATNHPIUS{cbsa}Q"
        metro_label = f"{city.title()} Metro"
    else:
        series_id = "USSTHPI"  # National index fallback
        metro_label = "U.S. National"
    result: dict = {"yoy_pct": None, "five_yr_pct": None, "values": [], "metro_label": metro_label}

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
                logger.warning(f"FRED API returned {resp.status_code}: {resp.text[:200]}")
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
        logger.warning(f"FHFA HPI fetch failed: {e}")

    return result


async def fetch_zip_appreciation(zip_codes: list[str]) -> dict[str, dict]:
    """Fetch ZIP-level home value appreciation from Zillow ZHVI public CSV.

    Uses Zillow's publicly hosted ZHVI (Zillow Home Value Index) data:
    Single-Family Homes, smoothed, seasonally adjusted, by ZIP code.

    Returns {zip_code: {"current": float, "yoy_pct": float, "five_yr_pct": float}} or empty.
    """
    import csv
    import io

    ZHVI_URL = (
        "https://files.zillowstatic.com/research/public_csvs/zhvi/"
        "Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
    )
    result: dict[str, dict] = {}
    target_zips = set(z.strip() for z in zip_codes)

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(ZHVI_URL)
            if resp.status_code != 200:
                logger.debug(f"ZHVI CSV fetch failed: HTTP {resp.status_code}")
                return result

            reader = csv.DictReader(io.StringIO(resp.text))
            # Column headers are dates like "2000-01-31", "2000-02-29", etc.
            # We need the last few date columns for appreciation calc
            fieldnames = reader.fieldnames or []
            date_cols = [c for c in fieldnames if c and len(c) == 10 and c[4] == "-"]
            if len(date_cols) < 2:
                return result

            date_cols.sort()  # chronological order
            latest_col = date_cols[-1]
            yoy_col = date_cols[-13] if len(date_cols) >= 13 else None  # ~12 months ago
            fiveyr_col = date_cols[-61] if len(date_cols) >= 61 else None  # ~60 months ago

            for row in reader:
                zip_code = row.get("RegionName", "").strip()
                if zip_code not in target_zips:
                    continue

                current_val = row.get(latest_col, "")
                if not current_val:
                    continue

                try:
                    current = float(current_val)
                except (ValueError, TypeError):
                    continue

                entry: dict = {"current": current, "yoy_pct": None, "five_yr_pct": None,
                               "as_of": latest_col}

                if yoy_col:
                    yoy_val = row.get(yoy_col, "")
                    if yoy_val:
                        try:
                            yoy_ref = float(yoy_val)
                            if yoy_ref > 0:
                                entry["yoy_pct"] = round((current - yoy_ref) / yoy_ref * 100, 1)
                        except (ValueError, TypeError):
                            pass

                if fiveyr_col:
                    fiveyr_val = row.get(fiveyr_col, "")
                    if fiveyr_val:
                        try:
                            fiveyr_ref = float(fiveyr_val)
                            if fiveyr_ref > 0:
                                entry["five_yr_pct"] = round(
                                    (current - fiveyr_ref) / fiveyr_ref * 100, 1
                                )
                        except (ValueError, TypeError):
                            pass

                result[zip_code] = entry

                if len(result) == len(target_zips):
                    break  # Found all requested ZIPs

    except Exception as e:
        logger.debug(f"ZHVI ZIP appreciation fetch failed: {e}")

    return result


async def fetch_population_growth(city: str, state: str) -> dict:
    """Fetch population estimates from Census Bureau ACS API.

    Uses the Census Population Estimates Program for place-level data.
    Falls back to county-level if place not found.

    Returns {"population": int, "yoy_change_pct": float, ...} or empty dict.
    """
    # Census state FIPS codes
    STATE_FIPS = {
        "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06",
        "CO": "08", "CT": "09", "DE": "10", "FL": "12", "GA": "13",
        "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19",
        "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
        "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29",
        "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34",
        "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
        "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45",
        "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50",
        "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56",
    }

    state_upper = state.strip().upper()
    state_fips = STATE_FIPS.get(state_upper)
    if not state_fips:
        return {}

    result: dict = {}
    census_api_key = os.environ.get("CENSUS_API_KEY", "")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Try Census Population Estimates API (most recent years)
            # PEP endpoint: population estimates for places
            base_url = "https://api.census.gov/data/2023/pep/population"
            params: dict[str, str] = {
                "get": "POP_2023,POP_2022,POP_2020,NAME",
                "for": "place:*",
                "in": f"state:{state_fips}",
            }
            if census_api_key:
                params["key"] = census_api_key

            resp = await client.get(base_url, params=params)

            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 1:
                    headers = data[0]
                    city_lower = city.strip().lower()

                    for row in data[1:]:
                        row_dict = dict(zip(headers, row))
                        name = row_dict.get("NAME", "")
                        # Census names like "Salt Lake City city, Utah"
                        place_name = name.split(",")[0].replace(" city", "").replace(" town", "")
                        if place_name.strip().lower() == city_lower:
                            try:
                                pop_2023 = int(row_dict.get("POP_2023", 0))
                                pop_2022 = int(row_dict.get("POP_2022", 0))
                                pop_2020 = int(row_dict.get("POP_2020", 0))

                                result["population"] = pop_2023
                                result["name"] = name.split(",")[0].strip()

                                if pop_2022 > 0:
                                    result["yoy_change_pct"] = round(
                                        (pop_2023 - pop_2022) / pop_2022 * 100, 2
                                    )
                                if pop_2020 > 0:
                                    result["three_yr_change_pct"] = round(
                                        (pop_2023 - pop_2020) / pop_2020 * 100, 2
                                    )
                                    result["pop_2020"] = pop_2020
                            except (ValueError, TypeError):
                                pass
                            break

            # If no place-level data, try county-level as fallback
            if not result:
                params_county: dict[str, str] = {
                    "get": "POP_2023,POP_2022,POP_2020,NAME",
                    "for": "county:*",
                    "in": f"state:{state_fips}",
                }
                if census_api_key:
                    params_county["key"] = census_api_key

                resp2 = await client.get(base_url, params=params_county)
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    if len(data2) > 1:
                        headers2 = data2[0]
                        for row in data2[1:]:
                            row_dict = dict(zip(headers2, row))
                            name = row_dict.get("NAME", "")
                            # Match county containing the city name
                            if city_lower in name.lower():
                                try:
                                    pop_2023 = int(row_dict.get("POP_2023", 0))
                                    pop_2022 = int(row_dict.get("POP_2022", 0))
                                    pop_2020 = int(row_dict.get("POP_2020", 0))

                                    result["population"] = pop_2023
                                    result["name"] = name.split(",")[0].strip()
                                    result["level"] = "county"

                                    if pop_2022 > 0:
                                        result["yoy_change_pct"] = round(
                                            (pop_2023 - pop_2022) / pop_2022 * 100, 2
                                        )
                                    if pop_2020 > 0:
                                        result["three_yr_change_pct"] = round(
                                            (pop_2023 - pop_2020) / pop_2020 * 100, 2
                                        )
                                        result["pop_2020"] = pop_2020
                                except (ValueError, TypeError):
                                    pass
                                break

    except Exception as e:
        logger.debug(f"Census population fetch failed: {e}")

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
