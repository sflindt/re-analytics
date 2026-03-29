"""Streamlit web app for re-analytics — professional real estate investment tool."""

from __future__ import annotations

import asyncio
import logging
import os
import statistics
from pathlib import Path

import streamlit as st
import pandas as pd

from re_analytics.models import (
    InvestmentParams,
    Listing,
    PropertyType,
    SearchCriteria,
    listings_to_csv,
)
from re_analytics.listing_finder import find_listings
from re_analytics.rates import get_current_rates, RateSnapshot

# Load .env
from dotenv import load_dotenv
load_dotenv()

# Quiet logging
logging.basicConfig(level=logging.WARNING)

# --- Page config ---
st.set_page_config(
    page_title="RE Analytics — Real Estate Investment Intelligence",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Custom CSS for professional look ---
st.markdown("""
<style>
    /* Clean header styling */
    .main .block-container {
        padding-top: 2rem;
        max-width: 1200px;
    }

    /* Metric cards */
    [data-testid="stMetric"] {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        border-radius: 8px;
        padding: 12px 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
        color: #6c757d !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.4rem !important;
        font-weight: 600 !important;
        color: #212529 !important;
    }

    /* Sidebar styling — white text on dark background */
    [data-testid="stSidebar"] {
        background-color: #1a1a2e;
    }
    [data-testid="stSidebar"] * {
        color: #e0e0e0 !important;
    }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stMarkdown h1,
    [data-testid="stSidebar"] .stMarkdown h2,
    [data-testid="stSidebar"] .stMarkdown h3,
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown strong,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] .stCaption {
        color: #e0e0e0 !important;
    }
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] select,
    [data-testid="stSidebar"] [data-baseweb="select"] {
        color: #212529 !important;
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2px;
        border-bottom: 2px solid #e9ecef;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 8px 20px;
        border-radius: 4px 4px 0 0;
    }

    /* Table styling */
    [data-testid="stDataFrame"] {
        border-radius: 8px;
        overflow: hidden;
    }

    /* Clean dividers */
    hr {
        border: none;
        border-top: 1px solid #e9ecef;
        margin: 1.5rem 0;
    }

    /* Header brand */
    .brand-header {
        font-size: 1.6rem;
        font-weight: 700;
        color: #1a1a2e;
        margin-bottom: 0;
        letter-spacing: -0.02em;
    }
    .brand-subtitle {
        font-size: 0.9rem;
        color: #6c757d;
        margin-top: 0;
    }
</style>
""", unsafe_allow_html=True)


# --- Helpers ---

def _run_async(coro):
    """Run async coroutine in sync context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _median(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def _listings_to_df(listings: list[Listing], params: InvestmentParams) -> pd.DataFrame:
    """Convert listings to a display DataFrame."""
    rows = []
    for l in listings:
        rows.append({
            "Address": l.address,
            "City": l.city,
            "State": l.state,
            "Zip": l.zip_code,
            "Price": l.price,
            "Beds": l.bedrooms,
            "Baths": l.bathrooms,
            "SqFt": l.sqft,
            "$/SqFt": round(l.price_per_sqft, 0) if l.price_per_sqft else None,
            "DOM": round(l.days_on_market, 0) if l.days_on_market else None,
            "Rent Mult": l.rent_multiplier(params.per_bed_rent),
            "PITI": round(l.monthly_piti(params), 0),
            "Year Built": l.year_built,
            "Status": l.status,
            "URL": l.listing_url,
            "Latitude": l.latitude,
            "Longitude": l.longitude,
        })
    return pd.DataFrame(rows)


# --- Sidebar ---

with st.sidebar:
    st.markdown("### RE Analytics")
    st.caption("Real Estate Investment Intelligence")

    st.markdown("---")

    st.markdown("**Search Parameters**")
    city = st.text_input("City", value="Salt Lake City")
    state = st.text_input("State", value="UT", max_chars=2)

    property_type = st.selectbox(
        "Property Type",
        ["Multi-family", "Single-family", "Any"],
        index=0,
    )
    type_map = {
        "Multi-family": PropertyType.MULTI_FAMILY,
        "Single-family": PropertyType.SINGLE_FAMILY,
        "Any": PropertyType.ANY,
    }

    col1, col2 = st.columns(2)
    with col1:
        min_price = st.number_input("Min Price", value=0, step=50_000, format="%d")
    with col2:
        max_price = st.number_input("Max Price", value=999_999_999, step=50_000, format="%d")

    search_area = st.slider("Search Radius (sq mi)", min_value=10, max_value=200, value=50)

    st.markdown("---")
    st.markdown("**Investment Assumptions**")
    per_bed_rent = st.number_input("Rent / Bed ($/mo)", value=600, step=50)
    down_pmt = st.slider("Down Payment %", min_value=5, max_value=50, value=25) / 100
    interest_rate = st.number_input("Interest Rate %", value=6.7, step=0.1, format="%.1f") / 100
    insurance_rate = st.number_input("Insurance Rate %", value=0.43, step=0.01, format="%.2f") / 100

    st.markdown("---")
    search_clicked = st.button("Search Properties", type="primary", use_container_width=True)

# --- State management ---

inv_params = InvestmentParams(
    per_bed_rent=float(per_bed_rent),
    down_pmt_pct=down_pmt,
    interest_rate=interest_rate,
    insurance_rate=insurance_rate,
)

if "listings" not in st.session_state:
    st.session_state.listings = []
if "rates" not in st.session_state:
    st.session_state.rates = None
if "search_city" not in st.session_state:
    st.session_state.search_city = ""
if "search_state" not in st.session_state:
    st.session_state.search_state = ""

if search_clicked:
    criteria = SearchCriteria(
        city=city,
        state=state.upper(),
        min_price=int(min_price),
        max_price=int(max_price),
        property_type=type_map[property_type],
        search_area_sqmi=search_area,
    )

    with st.spinner(f"Searching {property_type.lower()} properties in {city}, {state}..."):
        results = _run_async(find_listings(criteria))
        results.sort(key=lambda l: l.price)
        st.session_state.listings = results
        st.session_state.search_city = city
        st.session_state.search_state = state.upper()

    # Fetch rates in background
    with st.spinner("Loading rate environment..."):
        fred_key = os.environ.get("FRED_API_KEY")
        rates = _run_async(get_current_rates(fred_key))
        st.session_state.rates = rates

    if not results:
        st.warning("No listings found. Try broadening your search criteria.")

# --- Main content ---

listings = st.session_state.listings

if not listings:
    # Landing page
    st.markdown('<p class="brand-header">RE Analytics</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="brand-subtitle">Real estate investment intelligence — market analytics, property search, and rate insights.</p>',
        unsafe_allow_html=True,
    )

    st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("#### Market Analytics")
        st.markdown(
            "Median days on market, price per sqft, inventory levels, "
            "and price tier breakdowns for any market."
        )
    with col2:
        st.markdown("#### Property Search")
        st.markdown(
            "Search multi-family and single-family properties with investment "
            "metrics: PITI, rent multiplier, cap rate."
        )
    with col3:
        st.markdown("#### Rate Intelligence")
        st.markdown(
            "Current mortgage rates, Fed funds rate, Treasury spread, "
            "and payment scenario analysis."
        )

    st.markdown("---")
    st.info("Configure your search in the sidebar and click **Search Properties** to get started.")
    st.stop()


# --- Results header ---
search_city = st.session_state.search_city
search_state = st.session_state.search_state

st.markdown(f'<p class="brand-header">{search_city}, {search_state}</p>', unsafe_allow_html=True)
st.markdown(
    f'<p class="brand-subtitle">{len(listings)} properties found &mdash; {property_type.lower()}</p>',
    unsafe_allow_html=True,
)

# Tabs
tab_market, tab_listings, tab_map, tab_rates = st.tabs([
    "Market Analytics", "Listings", "Map", "Rate Environment"
])


# --- Tab 1: Market Analytics ---
with tab_market:
    active = [l for l in listings if l.status in ("Active", "FOR_SALE", None)]
    sold = [l for l in listings if l.status in ("Sold", "RECENTLY_SOLD")]

    # KPI metrics row
    col1, col2, col3, col4, col5 = st.columns(5)

    dom_vals = [l.days_on_market for l in active if l.days_on_market is not None]
    ppsf_vals = [l.price_per_sqft for l in active if l.price_per_sqft is not None]
    price_vals = [l.price for l in active if l.price > 0]
    stl_vals = [l.sale_to_list_ratio for l in sold if l.sale_to_list_ratio is not None]

    with col1:
        st.metric("Active Inventory", f"{len(active):,}")
    with col2:
        st.metric("Median DOM", f"{_median(dom_vals):.0f} days" if dom_vals else "N/A")
    with col3:
        st.metric("Median $/SqFt", f"${_median(ppsf_vals):,.0f}" if ppsf_vals else "N/A")
    with col4:
        st.metric("Median Price", f"${_median(price_vals):,.0f}" if price_vals else "N/A")
    with col5:
        st.metric("Sale/List Ratio", f"{_median(stl_vals):.1%}" if stl_vals else "N/A")

    st.markdown("---")

    # Two-column layout: price tier table + chart
    col_left, col_right = st.columns([1.2, 1])

    with col_left:
        st.markdown("#### Price Tier Breakdown")
        tiers = {
            "Under $300K": [l for l in listings if l.price < 300_000],
            "$300K - $500K": [l for l in listings if 300_000 <= l.price < 500_000],
            "$500K - $1M": [l for l in listings if 500_000 <= l.price < 1_000_000],
            "$1M+": [l for l in listings if l.price >= 1_000_000],
        }

        tier_rows = []
        for tier, group in tiers.items():
            if not group:
                tier_rows.append({
                    "Tier": tier, "Count": 0, "Med. DOM": None,
                    "Med. $/SqFt": None, "Med. Price": None, "Med. SqFt": None,
                })
                continue
            tier_rows.append({
                "Tier": tier,
                "Count": len(group),
                "Med. DOM": _median([l.days_on_market for l in group if l.days_on_market]),
                "Med. $/SqFt": _median([l.price_per_sqft for l in group if l.price_per_sqft]),
                "Med. Price": _median([l.price for l in group if l.price > 0]),
                "Med. SqFt": _median([l.sqft for l in group if l.sqft]),
            })

        tier_df = pd.DataFrame(tier_rows)
        st.dataframe(
            tier_df.style.format({
                "Med. DOM": lambda x: f"{x:.0f}" if pd.notna(x) else "—",
                "Med. $/SqFt": lambda x: f"${x:,.0f}" if pd.notna(x) else "—",
                "Med. Price": lambda x: f"${x:,.0f}" if pd.notna(x) else "—",
                "Med. SqFt": lambda x: f"{x:,.0f}" if pd.notna(x) else "—",
            }),
            use_container_width=True,
            hide_index=True,
        )

    with col_right:
        st.markdown("#### Price Distribution")
        prices = [l.price for l in listings if l.price > 0]
        if prices:
            # Create readable price buckets (e.g. "$500K", "$600K")
            bucket_size = max(50_000, round((max(prices) - min(prices)) / 12 / 50_000) * 50_000) or 100_000
            price_buckets = {}
            for p in prices:
                bucket = (p // bucket_size) * bucket_size
                if bucket >= 1_000_000:
                    label = f"${bucket / 1_000_000:.1f}M"
                else:
                    label = f"${int(bucket / 1000)}K"
                price_buckets[label] = price_buckets.get(label, 0) + 1
            chart_df = pd.DataFrame({"Price Range": list(price_buckets.keys()), "Count": list(price_buckets.values())})
            st.bar_chart(chart_df.set_index("Price Range"))

    st.markdown("---")

    # Second row: $/sqft distribution + beds breakdown
    col_left2, col_right2 = st.columns(2)

    with col_left2:
        st.markdown("#### $/SqFt Distribution")
        ppsf_data = [l.price_per_sqft for l in listings if l.price_per_sqft]
        if ppsf_data:
            # Create readable $/sqft buckets (e.g. "$200", "$250")
            bucket_size = max(25, round((max(ppsf_data) - min(ppsf_data)) / 10 / 25) * 25) or 50
            ppsf_buckets = {}
            for v in ppsf_data:
                bucket = int((v // bucket_size) * bucket_size)
                label = f"${bucket}"
                ppsf_buckets[label] = ppsf_buckets.get(label, 0) + 1
            ppsf_chart = pd.DataFrame({"$/SqFt Range": list(ppsf_buckets.keys()), "Count": list(ppsf_buckets.values())})
            st.bar_chart(ppsf_chart.set_index("$/SqFt Range"))
        else:
            st.info("No square footage data available.")

    with col_right2:
        st.markdown("#### By Bedroom Count")
        bed_counts = {}
        for l in listings:
            key = f"{l.bedrooms} bed" if l.bedrooms else "Unknown"
            bed_counts[key] = bed_counts.get(key, 0) + 1
        if bed_counts:
            bed_df = pd.DataFrame(
                {"Bedrooms": list(bed_counts.keys()), "Count": list(bed_counts.values())}
            ).sort_values("Bedrooms")
            st.bar_chart(bed_df.set_index("Bedrooms"))


# --- Tab 2: Listings ---
with tab_listings:
    df = _listings_to_df(listings, inv_params)

    # Summary row above table
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Listings", f"{len(listings):,}")
    with col2:
        avg_price = sum(l.price for l in listings) / len(listings) if listings else 0
        st.metric("Avg Price", f"${avg_price:,.0f}")
    with col3:
        with_sqft = [l for l in listings if l.sqft]
        avg_ppsf = (sum(l.price_per_sqft for l in with_sqft) / len(with_sqft)) if with_sqft else 0
        st.metric("Avg $/SqFt", f"${avg_ppsf:,.0f}")

    st.markdown("---")

    # Make URL displayable
    display_df = df.copy()
    if "URL" in display_df.columns:
        display_df["URL"] = display_df["URL"].apply(
            lambda x: x if pd.notna(x) and x else ""
        )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "URL": st.column_config.LinkColumn("Listing", display_text="View"),
            "Price": st.column_config.NumberColumn("Price", format="$%d"),
            "$/SqFt": st.column_config.NumberColumn("$/SqFt", format="$%d"),
            "DOM": st.column_config.NumberColumn("DOM", format="%d"),
            "Rent Mult": st.column_config.NumberColumn("Rent Mult", format="%d"),
            "PITI": st.column_config.NumberColumn("PITI", format="$%d"),
            "Latitude": None,
            "Longitude": None,
        },
    )

    # CSV download
    csv_data = listings_to_csv(listings, inv_params)
    if csv_data:
        st.download_button(
            label="Export to CSV",
            data=csv_data,
            file_name=f"listings_{search_city.lower().replace(' ', '_')}_{search_state.lower()}.csv",
            mime="text/csv",
        )


# --- Tab 3: Map ---
with tab_map:
    map_data = [
        {"lat": l.latitude, "lon": l.longitude, "address": l.address, "price": l.price}
        for l in listings
        if l.latitude and l.longitude
    ]

    if map_data:
        col1, col2 = st.columns([3, 1])
        with col1:
            map_df = pd.DataFrame(map_data)
            st.map(map_df, latitude="lat", longitude="lon", size=20)
        with col2:
            st.metric("Mapped", f"{len(map_data)}")
            st.metric("Total", f"{len(listings)}")
            coverage = len(map_data) / len(listings) * 100 if listings else 0
            st.metric("Coverage", f"{coverage:.0f}%")
    else:
        st.info("No listings with coordinates available for mapping.")


# --- Tab 4: Rate Environment ---
with tab_rates:
    rates: RateSnapshot | None = st.session_state.rates

    if rates is None:
        st.info("Rate data loads automatically when you run a search.")
        st.stop()

    st.markdown("#### Current Rate Environment")
    st.caption(f"Data as of: {rates.as_of or 'N/A'}")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("30-Year Fixed", f"{rates.mortgage_30yr:.2f}%" if rates.mortgage_30yr else "N/A")
    with col2:
        st.metric("15-Year Fixed", f"{rates.mortgage_15yr:.2f}%" if rates.mortgage_15yr else "N/A")
    with col3:
        st.metric("Fed Funds Rate", f"{rates.fed_funds_rate:.2f}%" if rates.fed_funds_rate else "N/A")
    with col4:
        st.metric("10-Year Treasury", f"{rates.treasury_10yr:.2f}%" if rates.treasury_10yr else "N/A")

    st.markdown("---")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("#### Rate Analysis")

        spread = rates.spread_over_treasury
        direction = rates.rate_direction

        analysis_data = {
            "Metric": [
                "Mortgage-Treasury Spread",
                "Rate Environment",
                "Historical Avg Spread",
            ],
            "Value": [
                f"{spread:.2f}%" if spread else "N/A",
                direction,
                "1.5% - 2.0%",
            ],
            "Signal": [
                "Wide (rates may compress)" if spread and spread > 2.0
                else "Normal" if spread and spread <= 2.0
                else "N/A",
                "Rates likely peaked" if direction == "Restrictive"
                else "Watch for cuts" if direction == "Moderately Restrictive"
                else direction,
                "Benchmark",
            ],
        }
        st.dataframe(pd.DataFrame(analysis_data), use_container_width=True, hide_index=True)

    with col_right:
        st.markdown("#### Payment Scenarios")
        st.caption("Monthly P&I on a 30-year fixed mortgage")

        # Use median price from search results as default loan
        med_price = _median([l.price for l in listings if l.price > 0])
        if med_price:
            loan_default = int(med_price * (1 - inv_params.down_pmt_pct))
        else:
            loan_default = 300_000

        loan_amount = st.number_input(
            "Loan Amount ($)",
            value=loan_default,
            step=25_000,
            format="%d",
            key="rate_loan_input",
        )

        scenarios = rates.monthly_payment_comparison(float(loan_amount))
        scenario_df = pd.DataFrame(
            {"Scenario": list(scenarios.keys()), "Monthly P&I": list(scenarios.values())}
        )
        scenario_df["Monthly P&I"] = scenario_df["Monthly P&I"].apply(lambda x: f"${x:,.0f}")
        st.dataframe(scenario_df, use_container_width=True, hide_index=True)

    st.markdown("---")

    st.markdown("#### What This Means for Investors")

    if rates.mortgage_30yr and rates.fed_funds_rate:
        if rates.fed_funds_rate >= 4.5:
            outlook = (
                "The Fed funds rate is elevated, suggesting the Fed is maintaining a restrictive "
                "stance to combat inflation. Mortgage rates tend to follow the broader rate "
                "environment with a lag. If inflation continues to moderate, rate cuts could "
                "follow, potentially improving borrowing costs over the next 12-24 months."
            )
        elif rates.fed_funds_rate >= 3.0:
            outlook = (
                "The Fed is in a moderately restrictive posture. Mortgage rates may have room "
                "to decline if economic data supports further easing. This could be a favorable "
                "entry point for investors who can lock in current rates and potentially refinance lower."
            )
        else:
            outlook = (
                "The rate environment is relatively accommodative. Lower rates support higher "
                "property valuations and better cash flow for investors. Consider locking in "
                "favorable financing terms."
            )

        spread = rates.spread_over_treasury or 0
        if spread > 2.5:
            spread_note = (
                f" The current mortgage-Treasury spread of {spread:.2f}% is wider than the historical "
                f"average of ~1.7%, suggesting mortgage rates have room to compress even without "
                f"Treasury yields falling."
            )
        elif spread > 1.5:
            spread_note = (
                f" The mortgage-Treasury spread of {spread:.2f}% is near historical norms, "
                f"meaning mortgage rate moves will largely track Treasury yields."
            )
        else:
            spread_note = ""

        st.markdown(f"{outlook}{spread_note}")
    else:
        st.markdown(
            "Rate data is limited. Check back after running a search to see the full rate analysis."
        )

    st.markdown("---")

    st.markdown("#### Data Sources")
    st.markdown(
        "- **30-Year Fixed Mortgage Rate** — [Freddie Mac PMMS](https://www.freddiemac.com/pmms) "
        "via [FRED Series MORTGAGE30US](https://fred.stlouisfed.org/series/MORTGAGE30US)\n"
        "- **15-Year Fixed Mortgage Rate** — [Freddie Mac PMMS](https://www.freddiemac.com/pmms) "
        "via [FRED Series MORTGAGE15US](https://fred.stlouisfed.org/series/MORTGAGE15US)\n"
        "- **Federal Funds Rate** — [Federal Reserve](https://www.federalreserve.gov/monetarypolicy/openmarket.htm) "
        "via [FRED Series FEDFUNDS](https://fred.stlouisfed.org/series/FEDFUNDS)\n"
        "- **10-Year Treasury Yield** — [U.S. Treasury](https://home.treasury.gov/resource-center/data-chart-center/interest-rates) "
        "via [FRED Series GS10](https://fred.stlouisfed.org/series/GS10)"
    )
    st.caption(
        "Rate data is sourced from the Federal Reserve Economic Data (FRED) API maintained by the "
        "Federal Reserve Bank of St. Louis. Updated weekly (mortgage rates) and monthly (Fed funds)."
    )
