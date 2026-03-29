"""Streamlit web app for re-analytics — professional real estate investment tool."""

from __future__ import annotations

import asyncio
import logging
import os
import statistics
from pathlib import Path

import numpy as np
import streamlit as st
import pandas as pd

from re_analytics.models import (
    InvestmentParams,
    Listing,
    PropertyType,
    SearchCriteria,
    calc_monthly_pmt,
    listings_to_csv,
)
from re_analytics.listing_finder import find_listings
from re_analytics.rates import get_current_rates, RateSnapshot
from re_analytics.cache import list_cached, load_cached_file
from re_analytics.report import generate_report

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

    /* Deal highlight cards */
    .metric-good [data-testid="stMetric"] {
        border-left: 4px solid #28a745;
    }
    .metric-warn [data-testid="stMetric"] {
        border-left: 4px solid #ffc107;
    }
    .metric-bad [data-testid="stMetric"] {
        border-left: 4px solid #dc3545;
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


def _score_percentile(value: float | None, values: list[float], lower_is_better: bool = True) -> float:
    """Return a 0-100 score for *value* relative to *values*.

    If *lower_is_better* is True a value at the bottom of the range scores 100
    and a value at the top scores 0. Reversed when lower_is_better=False.
    Returns 50 (neutral) when value is None or the range is zero.
    """
    clean = [v for v in values if v is not None]
    if value is None or len(clean) < 2:
        return 50.0
    lo, hi = min(clean), max(clean)
    if hi == lo:
        return 50.0
    pct = (value - lo) / (hi - lo)  # 0 = lowest, 1 = highest
    if lower_is_better:
        return round((1 - pct) * 100, 1)
    return round(pct * 100, 1)


def _compute_investment_scores(listings: list[Listing], params: InvestmentParams) -> list[float]:
    """Compute a 1-100 investment score for each listing.

    Weights:
        40% — Rent multiplier (lower is better)
        30% — $/SqFt vs median (lower is better)
        15% — DOM (higher = possibly more negotiable, higher is better)
        15% — PITI vs estimated rent (lower PITI / rent ratio = better)
    """
    if not listings:
        return []

    rent_mults = [l.rent_multiplier(params.per_bed_rent) for l in listings]
    ppsf_vals = [l.price_per_sqft for l in listings]
    dom_vals = [l.days_on_market for l in listings]

    piti_rent_ratios: list[float | None] = []
    for l in listings:
        piti = l.monthly_piti(params)
        rent_est = l.rent_estimate(params.per_bed_rent)
        if rent_est and rent_est > 0:
            piti_rent_ratios.append(piti / (rent_est / 12))
        else:
            piti_rent_ratios.append(None)

    scores = []
    for i, l in enumerate(listings):
        s_rent = _score_percentile(rent_mults[i], [v for v in rent_mults if v is not None], lower_is_better=True)
        s_ppsf = _score_percentile(ppsf_vals[i], [v for v in ppsf_vals if v is not None], lower_is_better=True)
        s_dom = _score_percentile(dom_vals[i], [v for v in dom_vals if v is not None], lower_is_better=False)
        s_piti = _score_percentile(piti_rent_ratios[i], [v for v in piti_rent_ratios if v is not None], lower_is_better=True)

        composite = 0.40 * s_rent + 0.30 * s_ppsf + 0.15 * s_dom + 0.15 * s_piti
        # Clamp to 1-100
        composite = max(1, min(100, round(composite)))
        scores.append(composite)
    return scores


def _score_indicator(score: float) -> str:
    """Return a colored circle indicator for a score value."""
    if score >= 75:
        return "🟢"
    elif score >= 40:
        return "🟡"
    return "🔴"


def _listings_to_df(listings: list[Listing], params: InvestmentParams) -> pd.DataFrame:
    """Convert listings to a display DataFrame."""
    scores = _compute_investment_scores(listings, params)
    rows = []
    for i, l in enumerate(listings):
        score = scores[i] if i < len(scores) else 50
        rows.append({
            "Score": score,
            "": _score_indicator(score),
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
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Score", ascending=False).reset_index(drop=True)
    return df


# --- Sidebar ---

with st.sidebar:
    st.markdown("### RE Analytics")
    st.caption("Real Estate Investment Intelligence")

    st.markdown("---")

    search_mode = st.radio(
        "I'm looking for a...",
        ["Investment Property", "Personal Home"],
        index=0,
        key="search_mode",
        horizontal=True,
    )
    is_investment = search_mode == "Investment Property"

    st.markdown("---")

    st.markdown("**Search**")
    city = st.text_input("City", value="Salt Lake City")
    state = st.text_input("State", value="UT", max_chars=2)

    property_type = st.selectbox(
        "Property Type",
        ["Multi-family", "Single-family", "Any"],
        index=0 if is_investment else 2,
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

    if is_investment:
        st.markdown("---")
        st.markdown("**Investment Assumptions**")
        per_bed_rent = st.number_input("Rent / Bed ($/mo)", value=600, step=50)
    else:
        per_bed_rent = 600

    st.markdown("---")
    search_clicked = st.button("Search Properties", type="primary", use_container_width=True)

    # --- Load previous results ---
    cached_searches = list_cached()
    if cached_searches:
        st.markdown("---")
        with st.expander("Load Previous Results"):
            cache_options = {
                f"{c['city']}, {c['state']} — {c['count']} listings ({c['age_hours']:.0f}h ago)": c["file"]
                for c in cached_searches[:10]
            }
            selected_cache = st.selectbox(
                "Previous searches",
                options=list(cache_options.keys()),
                key="cache_select",
            )
            if st.button("Load", key="load_cache_btn"):
                fname = cache_options[selected_cache]
                cached_listings = load_cached_file(fname)
                if cached_listings:
                    # Extract city/state from cache metadata
                    meta = next((c for c in cached_searches if c["file"] == fname), {})
                    st.session_state.listings = cached_listings
                    st.session_state.search_city = meta.get("city", city)
                    st.session_state.search_state = meta.get("state", state).upper()
                    # Also load rates
                    fred_key = os.environ.get("FRED_API_KEY")
                    st.session_state.rates = _run_async(get_current_rates(fred_key))
                    st.rerun()
                else:
                    st.error("Failed to load cached results.")

# --- State management ---

# Financing defaults — shown inline on relevant tabs, not sidebar
down_pmt = 0.25 if is_investment else 0.20
interest_rate = 0.067
insurance_rate = 0.0043

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

    # Fetch rates
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

header_col, actions_col = st.columns([3, 1])
with header_col:
    st.markdown(f'<p class="brand-header">{search_city}, {search_state}</p>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="brand-subtitle">{len(listings)} properties found &mdash; {property_type.lower()}</p>',
        unsafe_allow_html=True,
    )
with actions_col:
    # Financing adjustment (compact)
    with st.expander("Financing"):
        down_pmt = st.slider("Down Payment %", 5, 50, int(down_pmt * 100), key="fin_down") / 100
        interest_rate = st.number_input("Rate %", value=interest_rate * 100, step=0.1, format="%.1f", key="fin_rate") / 100
        insurance_rate = st.number_input("Insurance %", value=insurance_rate * 100, step=0.01, format="%.2f", key="fin_ins") / 100
    inv_params = InvestmentParams(
        per_bed_rent=float(per_bed_rent),
        down_pmt_pct=down_pmt,
        interest_rate=interest_rate,
        insurance_rate=insurance_rate,
    )

    # PDF + CSV downloads
    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        pdf_bytes = generate_report(
            listings, inv_params, search_city, search_state,
            is_investment=is_investment,
            rates=st.session_state.rates,
        )
        st.download_button(
            "PDF Report",
            data=pdf_bytes,
            file_name=f"re_analytics_{search_city.lower().replace(' ', '_')}_{search_state.lower()}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    with dl_col2:
        csv_data = listings_to_csv(listings, inv_params)
        if csv_data:
            st.download_button(
                "CSV Export",
                data=csv_data,
                file_name=f"listings_{search_city.lower().replace(' ', '_')}_{search_state.lower()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

# Tabs — mode-aware
if is_investment:
    tab_market, tab_listings, tab_map, tab_rates, tab_invest, tab_neighborhood = st.tabs([
        "Market Analytics", "Listings", "Map", "Rate Environment",
        "Investment Analysis", "Neighborhoods",
    ])
else:
    tab_market, tab_listings, tab_map, tab_rates = st.tabs([
        "Market Analytics", "Listings", "Map", "Rate Environment",
    ])
    tab_invest = None
    tab_neighborhood = None


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

    # --- Filters ---
    st.markdown("#### Filters")

    price_min_data = int(df["Price"].min()) if not df.empty else 0
    price_max_data = int(df["Price"].max()) if not df.empty else 1_000_000

    # Row 1: price + beds + sort
    fcol1, fcol2, fcol3 = st.columns([2, 1, 1])

    with fcol1:
        price_range = st.slider(
            "Price Range",
            min_value=price_min_data,
            max_value=price_max_data,
            value=(price_min_data, price_max_data),
            step=10_000,
            format="$%d",
            key="listings_price_range",
        )

    with fcol2:
        bed_options = sorted([int(b) for b in df["Beds"].dropna().unique()])
        min_beds = st.selectbox(
            "Min Bedrooms",
            options=[0] + bed_options,
            index=0,
            key="listings_min_beds",
        )

    with fcol3:
        if is_investment:
            sort_options = ["Score", "Price", "$/SqFt", "DOM", "Rent Mult"]
        else:
            sort_options = ["Price", "$/SqFt", "DOM", "SqFt", "Beds"]
        sort_by = st.selectbox(
            "Sort By",
            options=sort_options,
            index=0,
            key="listings_sort_by",
        )

    # Row 2: city filter + max payment (personal mode adds payment filter)
    cities_in_results = sorted(df["City"].dropna().unique().tolist()) if not df.empty else []
    fcol4, fcol5 = st.columns(2)

    with fcol4:
        selected_cities = st.multiselect(
            "Cities",
            options=cities_in_results,
            default=cities_in_results,
            key="listings_cities",
        )

    with fcol5:
        if not is_investment:
            max_piti_data = int(df["PITI"].max()) if not df.empty and "PITI" in df.columns else 10_000
            max_monthly = st.slider(
                "Max Monthly Payment (PITI)",
                min_value=500,
                max_value=max(max_piti_data + 500, 10_000),
                value=max(max_piti_data + 500, 10_000),
                step=100,
                format="$%d",
                key="listings_max_piti",
            )
        else:
            max_monthly = None

    # Apply filters
    filtered_df = df.copy()
    filtered_df = filtered_df[
        (filtered_df["Price"] >= price_range[0])
        & (filtered_df["Price"] <= price_range[1])
    ]
    if min_beds > 0:
        filtered_df = filtered_df[filtered_df["Beds"].fillna(0) >= min_beds]
    if selected_cities:
        filtered_df = filtered_df[filtered_df["City"].isin(selected_cities)]
    if max_monthly is not None:
        filtered_df = filtered_df[filtered_df["PITI"].fillna(0) <= max_monthly]

    # Apply sort
    sort_ascending = sort_by != "Score"  # Score: descending; others: ascending
    if sort_by in filtered_df.columns:
        filtered_df = filtered_df.sort_values(
            sort_by, ascending=sort_ascending, na_position="last"
        ).reset_index(drop=True)

    st.caption(f"Showing {len(filtered_df)} of {len(df)} listings")

    st.markdown("---")

    # Make URL displayable
    display_df = filtered_df.copy()
    if "URL" in display_df.columns:
        display_df["URL"] = display_df["URL"].apply(
            lambda x: x if pd.notna(x) and x else ""
        )

    # Column config — hide investment columns in personal mode
    col_cfg = {
        "Score": st.column_config.ProgressColumn(
            "Score", min_value=0, max_value=100, format="%d",
        ),
        "URL": st.column_config.LinkColumn("Listing", display_text="View"),
        "Price": st.column_config.NumberColumn("Price", format="$%,.0f"),
        "$/SqFt": st.column_config.NumberColumn("$/SqFt", format="$,.0f"),
        "DOM": st.column_config.NumberColumn("DOM", format=",.0f"),
        "PITI": st.column_config.NumberColumn("PITI", format="$%,.0f"),
        "Latitude": None,
        "Longitude": None,
    }
    if is_investment:
        col_cfg["Rent Mult"] = st.column_config.NumberColumn("Rent Mult", format="%,.0f")
    else:
        col_cfg["Score"] = None
        col_cfg[""] = None  # score indicator
        col_cfg["Rent Mult"] = None

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config=col_cfg,
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

    st.markdown("#### What This Means for " + ("Investors" if is_investment else "Buyers"))

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


# --- Tab 5: Investment Analysis (only in investment mode) ---
if tab_invest is not None:
    with tab_invest:
        st.markdown("#### Cash Flow Calculator")

        med_price = _median([l.price for l in listings if l.price > 0])
        default_price = int(med_price) if med_price else 400_000
        med_beds = _median([l.bedrooms for l in listings if l.bedrooms])
        default_rent = int((med_beds or 3) * inv_params.per_bed_rent)

        calc_col1, calc_col2 = st.columns(2)
        with calc_col1:
            calc_price = st.number_input(
                "Property Price ($)",
                value=default_price,
                step=25_000,
                format="%d",
                key="cf_price",
            )
            calc_rent = st.number_input(
                "Monthly Rent Estimate ($)",
                value=default_rent,
                step=100,
                format="%d",
                key="cf_rent",
            )

        # Compute cash flow metrics using sidebar financing params
        down_payment = calc_price * inv_params.down_pmt_pct
        loan_amount = calc_price - down_payment
        monthly_pi = calc_monthly_pmt(loan_amount, inv_params.interest_rate, 360)
        monthly_tax = calc_price * 0.0055 / 12  # use default tax rate
        monthly_ins = calc_price * inv_params.insurance_rate / 12
        monthly_piti = monthly_pi + monthly_tax + monthly_ins

        net_cash_flow = calc_rent - monthly_piti
        annual_net = net_cash_flow * 12
        cash_on_cash = (annual_net / down_payment * 100) if down_payment > 0 else 0
        breakeven_rent = monthly_piti

        with calc_col2:
            st.markdown("##### Monthly Breakdown")
            cf_data = {
                "Item": [
                    "Gross Rent",
                    "Principal & Interest",
                    "Property Tax",
                    "Insurance",
                    "Total PITI",
                    "**Net Cash Flow**",
                ],
                "Monthly": [
                    f"${calc_rent:,.0f}",
                    f"-${monthly_pi:,.0f}",
                    f"-${monthly_tax:,.0f}",
                    f"-${monthly_ins:,.0f}",
                    f"-${monthly_piti:,.0f}",
                    f"${net_cash_flow:,.0f}",
                ],
                "Annual": [
                    f"${calc_rent * 12:,.0f}",
                    f"-${monthly_pi * 12:,.0f}",
                    f"-${monthly_tax * 12:,.0f}",
                    f"-${monthly_ins * 12:,.0f}",
                    f"-${monthly_piti * 12:,.0f}",
                    f"${annual_net:,.0f}",
                ],
            }
            st.dataframe(pd.DataFrame(cf_data), use_container_width=True, hide_index=True)

        st.markdown("---")

        # Key metrics row
        kcol1, kcol2, kcol3 = st.columns(3)
        with kcol1:
            st.metric("Down Payment Required", f"${down_payment:,.0f}")
        with kcol2:
            st.metric("Cash-on-Cash Return", f"{cash_on_cash:.1f}%")
        with kcol3:
            st.metric("Break-Even Rent", f"${breakeven_rent:,.0f}/mo")

        st.markdown("---")

        # Sensitivity table
        st.markdown("#### Sensitivity Analysis")
        st.caption("Net monthly cash flow at different interest rates and rent levels")

        rate_scenarios = [
            inv_params.interest_rate - 0.01,
            inv_params.interest_rate,
            inv_params.interest_rate + 0.01,
            inv_params.interest_rate + 0.02,
        ]
        rent_scenarios = [
            int(calc_rent * 0.85),
            int(calc_rent * 0.95),
            calc_rent,
            int(calc_rent * 1.10),
        ]

        sensitivity_rows = []
        for r_rate in rate_scenarios:
            row = {"Rate": f"{r_rate * 100:.1f}%"}
            loan_amt = calc_price * (1 - inv_params.down_pmt_pct)
            pi = calc_monthly_pmt(loan_amt, r_rate, 360)
            piti = pi + monthly_tax + monthly_ins
            for rent_val in rent_scenarios:
                ncf = rent_val - piti
                row[f"Rent ${rent_val:,}"] = f"${ncf:,.0f}"
            sensitivity_rows.append(row)

        sens_df = pd.DataFrame(sensitivity_rows)
        st.dataframe(sens_df, use_container_width=True, hide_index=True)

        st.markdown("---")

        # Deal highlights
        st.markdown("#### Deal Highlights")
        st.caption("Properties flagged for strong investment signals")

        scores = _compute_investment_scores(listings, inv_params)
        median_ppsf = _median([l.price_per_sqft for l in listings if l.price_per_sqft])

        highlights = []
        for i, l in enumerate(listings):
            flags = []
            if median_ppsf and l.price_per_sqft and l.price_per_sqft < median_ppsf * 0.85:
                flags.append("Below median $/sqft")
            rm = l.rent_multiplier(inv_params.per_bed_rent)
            median_rm = _median([l2.rent_multiplier(inv_params.per_bed_rent) for l2 in listings if l2.rent_multiplier(inv_params.per_bed_rent)])
            if rm and median_rm and rm < median_rm * 0.85:
                flags.append("Strong rent multiple")
            piti_val = l.monthly_piti(inv_params)
            rent_est = l.rent_estimate(inv_params.per_bed_rent)
            if rent_est and rent_est > 0 and piti_val < rent_est / 12:
                flags.append("PITI < estimated rent")
            if flags:
                highlights.append({
                    "Address": l.address,
                    "Price": f"${l.price:,}",
                    "Score": scores[i] if i < len(scores) else "N/A",
                    "Signals": ", ".join(flags),
                })

        if highlights:
            st.dataframe(pd.DataFrame(highlights), use_container_width=True, hide_index=True)
        else:
            st.info("No standout deals found in the current results. Try broadening your search.")


# --- Tab 6: Neighborhood Comparison (only in investment mode) ---
if tab_neighborhood is not None:
    with tab_neighborhood:
        st.markdown("#### Neighborhood / Zip Code Comparison")
        st.caption("Aggregate statistics by zip code to compare areas at a glance")

        scores = _compute_investment_scores(listings, inv_params)

        zip_groups: dict[str, list[tuple[Listing, float]]] = {}
        for i, l in enumerate(listings):
            zc = l.zip_code or "Unknown"
            score = scores[i] if i < len(scores) else 50
            zip_groups.setdefault(zc, []).append((l, score))

        zip_rows = []
        for zc, group in sorted(zip_groups.items()):
            ls = [g[0] for g in group]
            sc = [g[1] for g in group]
            zip_rows.append({
                "Zip Code": zc,
                "Count": len(ls),
                "Median Price": _median([l.price for l in ls if l.price > 0]),
                "Median $/SqFt": _median([l.price_per_sqft for l in ls if l.price_per_sqft]),
                "Median DOM": _median([l.days_on_market for l in ls if l.days_on_market is not None]),
                "Avg Score": round(sum(sc) / len(sc), 1) if sc else None,
            })

        zip_df = pd.DataFrame(zip_rows)

        if not zip_df.empty:
            st.dataframe(
                zip_df.style.format({
                    "Median Price": lambda x: f"${x:,.0f}" if pd.notna(x) else "--",
                    "Median $/SqFt": lambda x: f"${x:,.0f}" if pd.notna(x) else "--",
                    "Median DOM": lambda x: f"{x:.0f}" if pd.notna(x) else "--",
                    "Avg Score": lambda x: f"{x:.1f}" if pd.notna(x) else "--",
                }),
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("---")
            st.markdown("#### Median Price by Zip Code")
            chart_data = zip_df[zip_df["Median Price"].notna()][["Zip Code", "Median Price"]].copy()
            if not chart_data.empty:
                st.bar_chart(chart_data.set_index("Zip Code"))
        else:
            st.info("No zip code data available.")
