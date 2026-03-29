"""PDF report generation for RE Analytics."""

from __future__ import annotations

import io
import statistics
from datetime import datetime

from fpdf import FPDF

from re_analytics.models import InvestmentParams, Listing
from re_analytics.rates import RateSnapshot


def _median(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


class REReport(FPDF):
    """Custom PDF with header/footer branding."""

    def __init__(self, city: str, state: str, is_investment: bool = True):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.city = city
        self.state = state
        self.is_investment = is_investment
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(26, 26, 46)
        self.cell(0, 8, "RE Analytics", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(108, 117, 125)
        mode = "Investment" if self.is_investment else "Personal Home"
        self.cell(
            0, 5,
            f"{self.city}, {self.state} -{mode} Report -{datetime.now().strftime('%B %d, %Y')}",
            new_x="LMARGIN", new_y="NEXT",
        )
        self.line(10, self.get_y() + 2, self.w - 10, self.get_y() + 2)
        self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(108, 117, 125)
        self.cell(0, 10, f"RE Analytics -Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title: str):
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(26, 26, 46)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def subsection_title(self, title: str):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(37, 99, 235)
        self.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def metric_row(self, metrics: list[tuple[str, str]]):
        """Render a row of key-value metric pairs."""
        col_w = (self.w - 20) / len(metrics)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(108, 117, 125)
        x_start = self.get_x()
        y = self.get_y()
        for label, _ in metrics:
            self.set_xy(x_start + metrics.index((label, _)) * col_w, y)
            self.cell(col_w, 4, label.upper(), align="L")
        self.ln(4)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(33, 37, 41)
        y2 = self.get_y()
        for i, (_, value) in enumerate(metrics):
            self.set_xy(x_start + i * col_w, y2)
            self.cell(col_w, 7, value, align="L")
        self.ln(10)

    def table(self, headers: list[str], rows: list[list[str]], col_widths: list[float] | None = None):
        """Render a simple table."""
        if col_widths is None:
            col_widths = [(self.w - 20) / len(headers)] * len(headers)

        # Header
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(248, 249, 250)
        self.set_text_color(33, 37, 41)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 6, h, border=1, fill=True, align="C")
        self.ln()

        # Rows
        self.set_font("Helvetica", "", 8)
        self.set_text_color(33, 37, 41)
        for row in rows:
            if self.get_y() > self.h - 25:
                self.add_page()
            for i, val in enumerate(row):
                self.cell(col_widths[i], 5, str(val), border=1, align="C")
            self.ln()
        self.ln(3)


def _add_market_commentary(
    pdf: REReport,
    listings: list[Listing],
    city: str,
    state: str,
    is_investment: bool,
    rates: RateSnapshot | None,
) -> None:
    """Add data-driven market commentary to the report."""

    active = [l for l in listings if l.status in ("Active", "FOR_SALE", None)]
    sold = [l for l in listings if l.status in ("Sold", "RECENTLY_SOLD")]
    dom_vals = [l.days_on_market for l in active if l.days_on_market is not None]
    ppsf_vals = [l.price_per_sqft for l in active if l.price_per_sqft is not None]
    price_vals = [l.price for l in active if l.price > 0]
    med_dom = _median(dom_vals) if dom_vals else None
    med_ppsf = _median(ppsf_vals) if ppsf_vals else None
    med_price = _median(price_vals) if price_vals else None

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(33, 37, 41)

    # --- Macro / National Context ---
    pdf.subsection_title("National Outlook")
    if rates and rates.mortgage_30yr and rates.fed_funds_rate:
        if rates.fed_funds_rate >= 4.5:
            macro = (
                f"The Federal Reserve is maintaining a restrictive monetary policy stance with "
                f"the federal funds rate at {rates.fed_funds_rate:.2f}%. The 30-year fixed "
                f"mortgage rate stands at {rates.mortgage_30yr:.2f}%, reflecting elevated "
                f"borrowing costs across the housing market. Historically, the Fed has maintained "
                f"restrictive rates until inflation sustainably returns to its 2% target. If "
                f"economic data continues to moderate, rate cuts over the next 12-24 months could "
                f"improve affordability for buyers and boost transaction volume."
            )
        elif rates.fed_funds_rate >= 3.0:
            macro = (
                f"The Fed funds rate at {rates.fed_funds_rate:.2f}% signals a moderately "
                f"restrictive stance, with the 30-year mortgage at {rates.mortgage_30yr:.2f}%. "
                f"The rate cycle appears to be transitioning -further easing could improve "
                f"housing affordability and stimulate demand. Buyers who lock in current rates "
                f"may benefit from refinancing opportunities as rates decline."
            )
        else:
            macro = (
                f"With the fed funds rate at {rates.fed_funds_rate:.2f}% and 30-year mortgages "
                f"at {rates.mortgage_30yr:.2f}%, the rate environment is relatively accommodative. "
                f"Lower rates support stronger property valuations and improved buyer purchasing power."
            )

        spread = rates.spread_over_treasury
        if spread and spread > 2.0:
            macro += (
                f" Notably, the mortgage-Treasury spread of {spread:.2f}% remains wider than "
                f"the historical average of approx. 1.7%, suggesting mortgage rates have room to compress "
                f"even without further Treasury yield declines -a positive signal for near-term "
                f"borrowing costs."
            )
    else:
        macro = (
            "National rate data was unavailable at the time of report generation. "
            "Monitor Federal Reserve policy decisions and Treasury yield movements for rate direction."
        )
    pdf.multi_cell(0, 4.5, macro)
    pdf.ln(4)

    # --- Local Market Assessment ---
    pdf.subsection_title(f"{city}, {state} Market Assessment")

    commentary_parts = []

    # Inventory assessment
    if len(active) > 0:
        if len(active) < 50:
            inv_signal = "tight"
            inv_note = "Limited inventory suggests a seller's market with competitive conditions for buyers."
        elif len(active) < 150:
            inv_signal = "moderate"
            inv_note = "Moderate inventory levels indicate a balanced market with options for both buyers and sellers."
        else:
            inv_signal = "elevated"
            inv_note = "Elevated inventory may provide buyers with more negotiating leverage and selection."
        commentary_parts.append(
            f"Active inventory stands at {len(active):,} listings -{inv_signal} for the market. {inv_note}"
        )

    # DOM assessment
    if med_dom is not None:
        if med_dom < 14:
            dom_note = (
                f"Properties are moving quickly with a median of {med_dom:.0f} days on market, "
                f"indicating strong demand. Buyers should be prepared to act fast on well-priced listings."
            )
        elif med_dom < 30:
            dom_note = (
                f"The median days on market of {med_dom:.0f} reflects a healthy pace of sales. "
                f"Properties are selling within a reasonable timeframe without sitting stale."
            )
        elif med_dom < 60:
            dom_note = (
                f"At {med_dom:.0f} median days on market, properties are taking longer to sell, "
                f"which may indicate buyer hesitancy or overpricing in some segments."
            )
        else:
            dom_note = (
                f"The median DOM of {med_dom:.0f} days signals a slower market. Buyers have leverage "
                f"to negotiate, and properties with extended DOM may be particularly negotiable."
            )
        commentary_parts.append(dom_note)

    # Price/sqft assessment
    if med_ppsf is not None and med_price is not None:
        commentary_parts.append(
            f"The market is pricing at a median of ${med_ppsf:,.0f} per square foot with a "
            f"median list price of ${med_price:,.0f}."
        )

    # Price tier analysis
    under_300 = [l for l in listings if l.price < 300_000]
    mid_tier = [l for l in listings if 300_000 <= l.price < 500_000]
    upper = [l for l in listings if 500_000 <= l.price < 1_000_000]
    luxury = [l for l in listings if l.price >= 1_000_000]

    if len(listings) > 10:
        dominant = max(
            [("under $300K", len(under_300)), ("$300K-$500K", len(mid_tier)),
             ("$500K-$1M", len(upper)), ("$1M+", len(luxury))],
            key=lambda x: x[1],
        )
        commentary_parts.append(
            f"The largest concentration of listings falls in the {dominant[0]} range "
            f"({dominant[1]} of {len(listings):,} total), defining the primary price band for this market."
        )

    # Investment-specific commentary
    if is_investment:
        listings_with_ppsf = [l for l in listings if l.price_per_sqft]
        if listings_with_ppsf and med_ppsf:
            below_median = [l for l in listings_with_ppsf if l.price_per_sqft < med_ppsf * 0.85]
            if below_median:
                commentary_parts.append(
                    f"For investors, {len(below_median)} listings are priced at least 15% below the "
                    f"median $/sqft of ${med_ppsf:,.0f}, potentially representing value opportunities "
                    f"worth deeper analysis."
                )

    for part in commentary_parts:
        pdf.multi_cell(0, 4.5, part)
        pdf.ln(2)

    # Key takeaway
    pdf.ln(2)
    pdf.subsection_title("Key Takeaway")
    if is_investment:
        if med_dom and med_dom > 30 and len(active) > 50:
            takeaway = (
                f"Elevated inventory and longer days on market create a favorable environment "
                f"for investor negotiations. Focus on properties with DOM above {med_dom:.0f} days "
                f"and $/sqft below ${med_ppsf:,.0f} for the strongest value plays."
            )
        elif med_dom and med_dom < 14:
            takeaway = (
                f"Fast-moving inventory means competitive offers are essential. Pre-approved financing "
                f"and quick due diligence will be critical to securing investment properties."
            )
        else:
            takeaway = (
                f"Market conditions support active sourcing. "
                f"Evaluate each deal on cash flow fundamentals -rent multiplier, PITI coverage, "
                f"and potential for appreciation."
            )
    else:
        if med_dom and med_dom > 30:
            takeaway = (
                f"Buyers have time and negotiating room in this market. Don't rush -use the "
                f"longer selling times as leverage to negotiate below asking price."
            )
        elif med_dom and med_dom < 14:
            takeaway = (
                f"This is a fast market. Get pre-approved, know your budget ceiling, and be "
                f"prepared to make strong offers quickly when you find the right home."
            )
        else:
            takeaway = (
                f"A balanced market with reasonable selection. Take time to compare options "
                f"across neighborhoods and price points to find the best value."
            )
    pdf.multi_cell(0, 4.5, takeaway)
    pdf.ln(3)


def generate_report(
    listings: list[Listing],
    params: InvestmentParams,
    city: str,
    state: str,
    is_investment: bool = True,
    rates: RateSnapshot | None = None,
) -> bytes:
    """Generate a professional PDF report and return as bytes."""

    pdf = REReport(city, state, is_investment)
    pdf.alias_nb_pages()
    pdf.add_page()

    active = [l for l in listings if l.status in ("Active", "FOR_SALE", None)]
    sold = [l for l in listings if l.status in ("Sold", "RECENTLY_SOLD")]

    # --- Page 1: Market Overview ---
    pdf.section_title("Market Overview")

    dom_vals = [l.days_on_market for l in active if l.days_on_market is not None]
    ppsf_vals = [l.price_per_sqft for l in active if l.price_per_sqft is not None]
    price_vals = [l.price for l in active if l.price > 0]

    pdf.metric_row([
        ("Active Inventory", f"{len(active):,}"),
        ("Median DOM", f"{_median(dom_vals):.0f} days" if dom_vals else "N/A"),
        ("Median $/SqFt", f"${_median(ppsf_vals):,.0f}" if ppsf_vals else "N/A"),
        ("Median Price", f"${_median(price_vals):,.0f}" if price_vals else "N/A"),
    ])

    # Price tier breakdown
    pdf.subsection_title("Price Tier Breakdown")
    tiers = {
        "Under $300K": [l for l in listings if l.price < 300_000],
        "$300K-$500K": [l for l in listings if 300_000 <= l.price < 500_000],
        "$500K-$1M": [l for l in listings if 500_000 <= l.price < 1_000_000],
        "$1M+": [l for l in listings if l.price >= 1_000_000],
    }
    tier_rows = []
    for tier, group in tiers.items():
        if not group:
            tier_rows.append([tier, "0", "-", "-", "-"])
            continue
        tier_rows.append([
            tier,
            str(len(group)),
            f"{_median([l.days_on_market for l in group if l.days_on_market]):.0f}" if any(l.days_on_market for l in group) else "-",
            f"${_median([l.price_per_sqft for l in group if l.price_per_sqft]):,.0f}" if any(l.price_per_sqft for l in group) else "-",
            f"${_median([l.price for l in group if l.price > 0]):,.0f}" if group else "-",
        ])
    pdf.table(
        ["Tier", "Count", "Med. DOM", "Med. $/SqFt", "Med. Price"],
        tier_rows,
        [40, 25, 30, 35, 40],
    )

    # --- Rate Environment (if available) ---
    if rates and rates.mortgage_30yr:
        pdf.section_title("Rate Environment")
        rate_metrics = [
            ("30-Year Fixed", f"{rates.mortgage_30yr:.2f}%"),
            ("15-Year Fixed", f"{rates.mortgage_15yr:.2f}%" if rates.mortgage_15yr else "N/A"),
            ("Fed Funds Rate", f"{rates.fed_funds_rate:.2f}%" if rates.fed_funds_rate else "N/A"),
            ("10-Year Treasury", f"{rates.treasury_10yr:.2f}%" if rates.treasury_10yr else "N/A"),
        ]
        pdf.metric_row(rate_metrics)

        spread = rates.spread_over_treasury
        if spread:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(33, 37, 41)
            signal = "wider than historical average -rates may compress" if spread > 2.0 else "near historical norms"
            pdf.multi_cell(0, 5, f"Mortgage-Treasury spread: {spread:.2f}% ({signal})")
            pdf.ln(3)

    # --- Market Commentary ---
    pdf.add_page()
    pdf.section_title("Market Commentary")

    _add_market_commentary(pdf, listings, city, state, is_investment, rates)

    # --- Listings Table ---
    pdf.add_page()
    pdf.section_title(f"Listings ({len(listings):,})")

    if is_investment:
        headers = ["Address", "Price", "Beds", "SqFt", "$/SqFt", "DOM", "PITI", "Rent Mult"]
        widths = [70, 30, 18, 25, 25, 20, 28, 28]
    else:
        headers = ["Address", "Price", "Beds", "Baths", "SqFt", "$/SqFt", "DOM", "PITI"]
        widths = [70, 30, 18, 18, 25, 25, 20, 28]

    rows = []
    for l in listings[:50]:  # Cap at 50 for PDF readability
        ppsf = f"${l.price_per_sqft:,.0f}" if l.price_per_sqft else "-"
        dom = f"{l.days_on_market:.0f}" if l.days_on_market else "-"
        piti = f"${l.monthly_piti(params):,.0f}"
        if is_investment:
            rm = l.rent_multiplier(params.per_bed_rent)
            rows.append([
                l.address[:35],
                f"${l.price:,}",
                str(l.bedrooms or "-"),
                f"{l.sqft:,}" if l.sqft else "-",
                ppsf, dom, piti,
                f"{rm:,.0f}" if rm else "-",
            ])
        else:
            rows.append([
                l.address[:35],
                f"${l.price:,}",
                str(l.bedrooms or "-"),
                str(l.bathrooms or "-"),
                f"{l.sqft:,}" if l.sqft else "-",
                ppsf, dom, piti,
            ])

    pdf.table(headers, rows, widths)

    if len(listings) > 50:
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(108, 117, 125)
        pdf.cell(0, 5, f"Showing top 50 of {len(listings)} listings. Full data available in CSV export.")
        pdf.ln(5)

    # --- Zip code breakdown ---
    zip_groups: dict[str, list[Listing]] = {}
    for l in listings:
        z = l.zip_code or "Unknown"
        zip_groups.setdefault(z, []).append(l)

    if len(zip_groups) > 1:
        pdf.add_page()
        pdf.section_title("Neighborhood Comparison")
        zip_rows = []
        for z in sorted(zip_groups.keys()):
            group = zip_groups[z]
            zip_rows.append([
                z,
                str(len(group)),
                f"${_median([l.price for l in group if l.price > 0]):,.0f}" if group else "-",
                f"${_median([l.price_per_sqft for l in group if l.price_per_sqft]):,.0f}" if any(l.price_per_sqft for l in group) else "-",
                f"{_median([l.days_on_market for l in group if l.days_on_market]):.0f}" if any(l.days_on_market for l in group) else "-",
            ])
        pdf.table(
            ["Zip Code", "Count", "Med. Price", "Med. $/SqFt", "Med. DOM"],
            zip_rows,
            [30, 20, 35, 35, 25],
        )

    # --- Disclaimer ---
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(108, 117, 125)
    pdf.multi_cell(0, 4, (
        "This report is generated by RE Analytics for informational purposes only. "
        "Data is sourced from public listing services and may not be fully accurate or current. "
        "Investment calculations are estimates based on the assumptions provided and should not be "
        "considered financial advice. Consult a licensed real estate professional before making "
        "purchasing decisions."
    ))

    return pdf.output()
