"""PDF report generation for RE Analytics - professional branded design."""

from __future__ import annotations

import math
import statistics
from datetime import datetime

from fpdf import FPDF

from re_analytics.models import InvestmentParams, Listing
from re_analytics.rates import RateSnapshot

# Brand colors
NAVY = (26, 26, 46)
BLUE = (37, 99, 235)
LIGHT_BLUE = (219, 234, 254)
DARK_TEXT = (33, 37, 41)
MID_TEXT = (75, 85, 99)
LIGHT_TEXT = (108, 117, 125)
WHITE = (255, 255, 255)
BG_LIGHT = (248, 250, 252)
BG_TABLE_HEADER = (30, 58, 95)
BG_ROW_ALT = (245, 247, 250)
ACCENT_GREEN = (16, 185, 129)
ACCENT_BAR = (37, 99, 235)
DIVIDER = (226, 232, 240)


def _median(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def _dom_pace(median_dom: float | None) -> str:
    """Categorize days-on-market into a pace label."""
    if median_dom is None:
        return ""
    if median_dom < 14:
        return "Fast"
    if median_dom < 30:
        return "Normal"
    if median_dom < 60:
        return "Slow"
    return "Stale"


def _normalize_status(listing) -> str:
    """Normalize listing status to Active/Sold/Pending."""
    if listing.sold_price and listing.sold_price > 0:
        return "Sold"
    raw = (listing.status or "").upper().replace("_", " ")
    if raw in ("ACTIVE", "FOR SALE", ""):
        return "Active"
    if "PENDING" in raw:
        return "Pending"
    return listing.status or "Active"


def _dynamic_buckets(values: list[float], num_buckets: int = 5) -> list[tuple[str, float, float]]:
    """Create clean, dynamic buckets from actual data distribution.

    Returns list of (label, lo, hi) tuples with human-friendly boundaries.
    """
    vals = sorted(v for v in values if v is not None and v > 0)
    if len(vals) < 3:
        return []

    lo = min(vals)
    hi = max(vals)
    spread = hi - lo

    # Pick a clean rounding step based on the data range
    if spread > 500_000:
        step_round = 100_000
    elif spread > 200_000:
        step_round = 50_000
    elif spread > 50_000:
        step_round = 25_000
    elif spread > 10_000:
        step_round = 5_000
    elif spread > 1_000:
        step_round = 500
    elif spread > 100:
        step_round = 25
    else:
        step_round = 10

    # Round lo down, hi up to clean boundaries
    bucket_lo = math.floor(lo / step_round) * step_round
    bucket_hi = math.ceil(hi / step_round) * step_round

    # Calculate step size, snapped to clean increments
    raw_step = (bucket_hi - bucket_lo) / num_buckets
    step = max(step_round, math.ceil(raw_step / step_round) * step_round)

    buckets = []
    current = bucket_lo
    while current < bucket_hi:
        next_val = current + step
        # Format label
        if step_round >= 1_000:
            def _fmt(v):
                if v >= 1_000_000:
                    return f"${v / 1_000_000:.1f}M" if v % 1_000_000 else f"${v // 1_000_000}M"
                return f"${v // 1_000:,}K"
        else:
            def _fmt(v):
                return f"${v:,.0f}"
        label = f"{_fmt(current)}-{_fmt(next_val)}"
        buckets.append((label, current, next_val))
        current = next_val

    return buckets


class REReport(FPDF):
    """Professional branded PDF report."""

    def __init__(self, city: str, state: str, is_investment: bool = True):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.city = city
        self.state = state
        self.is_investment = is_investment
        self.is_cover = False
        self.set_auto_page_break(auto=True, margin=25)
        self.set_margins(left=18, top=18, right=18)

    def header(self):
        if self.is_cover:
            return
        # Top accent bar
        self.set_fill_color(*ACCENT_BAR)
        self.rect(0, 0, self.w, 3, "F")

        # Header bar
        self.set_y(8)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*NAVY)
        self.cell(0, 5, "RE ANALYTICS", align="L")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*LIGHT_TEXT)
        self.cell(0, 5, f"{self.city}, {self.state}", align="R", new_x="LMARGIN", new_y="NEXT")

        # Thin divider line
        y = self.get_y() + 1
        self.set_draw_color(*DIVIDER)
        self.set_line_width(0.3)
        self.line(18, y, self.w - 18, y)
        self.set_y(y + 4)

    def footer(self):
        if self.is_cover:
            return
        self.set_y(-18)
        # Thin line above footer
        self.set_draw_color(*DIVIDER)
        self.set_line_width(0.3)
        self.line(18, self.get_y(), self.w - 18, self.get_y())
        self.ln(2)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*LIGHT_TEXT)
        self.cell(0, 4, "RE Analytics  |  Confidential", align="L")
        self.cell(0, 4, f"Page {self.page_no()}", align="R")

    # --- Drawing primitives ---

    def accent_bar(self, width: float = 35, height: float = 2.5):
        """Draw a short colored accent bar below current position."""
        self.set_fill_color(*ACCENT_BAR)
        self.rect(self.get_x(), self.get_y(), width, height, "F")
        self.ln(height + 3)

    def section_title(self, title: str):
        self.ln(4)
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*NAVY)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.accent_bar()

    def subsection_title(self, title: str):
        self.ln(2)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*BLUE)
        self.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body_text(self, text: str):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*DARK_TEXT)
        self.multi_cell(0, 4.8, text)
        self.ln(2)

    def metric_cards(self, metrics: list[tuple[str, str]], per_row: int = 4):
        """Render metrics as card-style boxes with light background."""
        margin = 18
        gap = 4
        available = self.w - 2 * margin
        card_w = (available - (per_row - 1) * gap) / per_row
        card_h = 18

        x_start = margin
        y_start = self.get_y()

        for idx, (label, value) in enumerate(metrics):
            col = idx % per_row
            row = idx // per_row
            x = x_start + col * (card_w + gap)
            y = y_start + row * (card_h + gap)

            # Card background
            self.set_fill_color(*BG_LIGHT)
            self.set_draw_color(*DIVIDER)
            self.set_line_width(0.3)
            self.rect(x, y, card_w, card_h, "DF")

            # Top accent line on card
            self.set_fill_color(*ACCENT_BAR)
            self.rect(x, y, card_w, 1.2, "F")

            # Label
            self.set_xy(x + 3, y + 3)
            self.set_font("Helvetica", "", 6.5)
            self.set_text_color(*LIGHT_TEXT)
            self.cell(card_w - 6, 3.5, label.upper(), align="L")

            # Value
            self.set_xy(x + 3, y + 8)
            self.set_font("Helvetica", "B", 12)
            self.set_text_color(*NAVY)
            self.cell(card_w - 6, 7, value, align="L")

        total_rows = (len(metrics) + per_row - 1) // per_row
        self.set_y(y_start + total_rows * (card_h + gap) + 2)

    def styled_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        col_widths: list[float] | None = None,
        align: list[str] | None = None,
        links: list[str] | None = None,
    ):
        """Render a professional table. Optionally adds a clickable 'View' link column."""
        if not headers:
            return
        margin = 18
        available = self.w - 2 * margin

        link_col_w = 18 if links else 0
        if col_widths is None:
            data_w = available - link_col_w
            col_widths = [data_w / len(headers)] * len(headers)

        if align is None:
            align = ["C"] * len(headers)

        all_headers = headers + (["Link"] if links else [])
        all_widths = list(col_widths) + ([link_col_w] if links else [])
        all_aligns = list(align) + (["C"] if links else [])

        row_h = 6

        def _draw_header():
            self.set_font("Helvetica", "B", 7.5)
            self.set_fill_color(*BG_TABLE_HEADER)
            self.set_text_color(*WHITE)
            self.set_draw_color(*BG_TABLE_HEADER)
            self.set_line_width(0.1)
            for i, h in enumerate(all_headers):
                self.cell(all_widths[i], row_h + 1, h, border=0, fill=True, align="C")
            self.ln()

        _draw_header()

        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*DARK_TEXT)
        for r_idx, row in enumerate(rows):
            if self.get_y() > self.h - 30:
                self.add_page()
                _draw_header()
                self.set_font("Helvetica", "", 7.5)
                self.set_text_color(*DARK_TEXT)

            self.set_fill_color(*(BG_ROW_ALT if r_idx % 2 == 1 else WHITE))

            for i, val in enumerate(row):
                self.cell(all_widths[i], row_h, str(val), border=0, fill=True, align=all_aligns[i])

            # Optional link cell
            if links:
                url = links[r_idx] if r_idx < len(links) else ""
                if url:
                    self.set_text_color(*BLUE)
                    self.set_font("Helvetica", "U", 7.5)
                    self.cell(link_col_w, row_h, "View", border=0, fill=True, align="C", link=url)
                    self.set_font("Helvetica", "", 7.5)
                    self.set_text_color(*DARK_TEXT)
                else:
                    self.cell(link_col_w, row_h, "-", border=0, fill=True, align="C")
            self.ln()

        self.set_draw_color(*DIVIDER)
        self.set_line_width(0.3)
        self.line(margin, self.get_y(), margin + sum(all_widths), self.get_y())
        self.ln(4)

    def callout_box(self, title: str, text: str):
        """Render a highlighted callout box with left accent border."""
        margin = 18
        box_w = self.w - 2 * margin
        text_w = box_w - 14
        line_h = 4.5

        # Measure actual text height using fpdf2
        self.set_font("Helvetica", "", 8.5)
        text_h = self.multi_cell(text_w, line_h, text, dry_run=True, output="HEIGHT")
        box_h = max(18, 10 + text_h)

        x = margin
        y = self.get_y()

        # Page break if needed
        if y + box_h > self.h - 25:
            self.add_page()
            y = self.get_y()

        # Background + left accent bar
        self.set_fill_color(*BG_LIGHT)
        self.rect(x, y, box_w, box_h, "F")
        self.set_fill_color(*ACCENT_BAR)
        self.rect(x, y, 3, box_h, "F")

        # Title
        self.set_xy(x + 7, y + 3)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*BLUE)
        self.cell(text_w, 5, title, align="L")

        # Text
        self.set_xy(x + 7, y + 9)
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(*DARK_TEXT)
        self.multi_cell(text_w, line_h, text)

        self.set_y(y + box_h + 4)


# ---- Commentary logic ----

def _build_commentary(
    listings: list[Listing], city: str, state: str,
    is_investment: bool, rates: RateSnapshot | None,
) -> dict[str, str]:
    """Build market commentary sections as a dict of title -> text."""

    active = [l for l in listings if l.status in ("Active", "FOR_SALE", None)]
    dom_vals = [l.days_on_market for l in active if l.days_on_market is not None]
    ppsf_vals = [l.price_per_sqft for l in active if l.price_per_sqft is not None]
    price_vals = [l.price for l in active if l.price > 0]
    med_dom = _median(dom_vals) if dom_vals else None
    med_ppsf = _median(ppsf_vals) if ppsf_vals else None
    med_price = _median(price_vals) if price_vals else None

    sections: dict[str, str] = {}

    # National
    if rates and rates.mortgage_30yr and rates.fed_funds_rate:
        if rates.fed_funds_rate >= 4.5:
            if is_investment:
                macro = (
                    f"The Federal Reserve is maintaining a restrictive monetary policy with the "
                    f"federal funds rate at {rates.fed_funds_rate:.2f}%. The 30-year fixed mortgage "
                    f"rate stands at {rates.mortgage_30yr:.2f}%, increasing carrying costs and "
                    f"compressing cash flow margins. If inflation moderates, rate cuts over the "
                    f"next 12-24 months could improve deal economics."
                )
            else:
                macro = (
                    f"The Federal Reserve is maintaining a restrictive monetary policy with the "
                    f"federal funds rate at {rates.fed_funds_rate:.2f}%. The 30-year fixed mortgage "
                    f"rate stands at {rates.mortgage_30yr:.2f}%, which impacts monthly payments "
                    f"and purchasing power. If inflation moderates, rate cuts over the next 12-24 "
                    f"months could improve affordability."
                )
        elif rates.fed_funds_rate >= 3.0:
            macro = (
                f"The Fed funds rate at {rates.fed_funds_rate:.2f}% signals a moderately "
                f"restrictive stance, with the 30-year mortgage at {rates.mortgage_30yr:.2f}%. "
                f"The rate cycle appears to be transitioning. Buyers who lock in current rates "
                f"may benefit from refinancing opportunities as rates decline."
            )
        else:
            macro = (
                f"With the fed funds rate at {rates.fed_funds_rate:.2f}% and 30-year mortgages "
                f"at {rates.mortgage_30yr:.2f}%, the rate environment is accommodative. "
                f"Lower rates support stronger purchasing power and more favorable monthly payments."
            )
        spread = rates.spread_over_treasury
        if spread and spread > 2.0:
            macro += (
                f" The mortgage-Treasury spread of {spread:.2f}% is wider than the historical "
                f"average of 1.7%, suggesting mortgage rates may compress further."
            )
        sections["National Outlook"] = macro
    else:
        sections["National Outlook"] = (
            "National rate data was unavailable at the time of report generation."
        )

    # Local
    local_parts = []
    if len(active) > 0:
        if len(active) < 50:
            local_parts.append(
                f"Active inventory of {len(active):,} listings indicates a tight, seller-favoring market."
            )
        elif len(active) < 150:
            local_parts.append(
                f"Moderate inventory of {len(active):,} listings suggests a balanced market."
            )
        else:
            local_parts.append(
                f"Elevated inventory of {len(active):,} listings provides buyer negotiating leverage."
            )

    if med_dom is not None:
        if med_dom < 14:
            local_parts.append(f"Median DOM of {med_dom:.0f} days reflects strong demand.")
        elif med_dom < 30:
            local_parts.append(f"Median DOM of {med_dom:.0f} days reflects a healthy sales pace.")
        elif med_dom < 60:
            local_parts.append(f"At {med_dom:.0f} median DOM, properties are taking longer to sell.")
        else:
            local_parts.append(f"Median DOM of {med_dom:.0f} days signals a slower, buyer-friendly market.")

    if med_ppsf and med_price:
        local_parts.append(
            f"Market pricing at ${med_ppsf:,.0f}/sqft with median list price of ${med_price:,.0f}."
        )

    sections[f"{city}, {state} Market"] = " ".join(local_parts)

    # Takeaway
    if is_investment:
        if med_dom and med_dom > 30:
            sections["Key Takeaway"] = (
                f"Longer days on market create negotiation opportunities. Target properties "
                f"with DOM above {med_dom:.0f} days and $/sqft below ${med_ppsf:,.0f} for value plays."
            )
        else:
            sections["Key Takeaway"] = (
                "Evaluate each deal on cash flow fundamentals: rent multiplier, PITI coverage, "
                "and potential for appreciation. Pre-approved financing enables faster execution."
            )
    else:
        if med_dom and med_dom > 30:
            sections["Key Takeaway"] = (
                f"Properties are averaging {med_dom:.0f} days on market, giving buyers time to "
                f"evaluate options and negotiate. Homes priced below ${med_ppsf:,.0f}/sqft "
                f"may represent the best value relative to the neighborhood."
                if med_ppsf else
                f"Properties are averaging {med_dom:.0f} days on market, giving buyers time to "
                f"evaluate options and negotiate on price."
            )
        else:
            sections["Key Takeaway"] = (
                "The market is moving quickly. Get pre-approved, know your budget ceiling, "
                "and be ready to act. Compare options across neighborhoods and price points "
                "to find the right home at the right price."
            )

    return sections


# ---- Main generator ----

def generate_report(
    listings: list[Listing],
    params: InvestmentParams,
    city: str,
    state: str,
    is_investment: bool = True,
    rates: RateSnapshot | None = None,
    target_price: int | None = None,
    target_beds: int | None = None,
    appreciation: dict | None = None,
    area_news: str | None = None,
) -> bytes:
    """Generate a professional PDF report."""

    pdf = REReport(city, state, is_investment)
    pdf.alias_nb_pages()

    # ==================== COVER PAGE ====================
    pdf.is_cover = True
    pdf.add_page()

    # Full-width navy bar at top
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, 95, "F")

    # Blue accent strip
    pdf.set_fill_color(*ACCENT_BAR)
    pdf.rect(0, 95, pdf.w, 4, "F")

    # Title text on navy background
    pdf.set_y(30)
    pdf.set_font("Helvetica", "B", 32)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 14, "RE Analytics", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(180, 200, 230)
    subtitle = "Investment Property Intelligence" if is_investment else "Home Buying Intelligence"
    pdf.cell(0, 8, subtitle, align="C", new_x="LMARGIN", new_y="NEXT")

    # Divider line on cover
    pdf.ln(8)
    y_line = pdf.get_y()
    pdf.set_draw_color(80, 120, 180)
    pdf.set_line_width(0.5)
    center_x = pdf.w / 2
    pdf.line(center_x - 30, y_line, center_x + 30, y_line)

    # City / State
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 12, f"{city}, {state}", align="C", new_x="LMARGIN", new_y="NEXT")

    # Mode + date below navy bar
    pdf.set_y(110)
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(*DARK_TEXT)
    mode = "Investment Analysis" if is_investment else "Personal Home Search"
    pdf.cell(0, 8, mode, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*LIGHT_TEXT)
    pdf.cell(0, 6, datetime.now().strftime("%B %d, %Y"), align="C", new_x="LMARGIN", new_y="NEXT")

    # Summary stats on cover
    active = [l for l in listings if l.status in ("Active", "FOR_SALE", None)]
    dom_vals = [l.days_on_market for l in active if l.days_on_market is not None]
    ppsf_vals = [l.price_per_sqft for l in active if l.price_per_sqft is not None]
    price_vals = [l.price for l in active if l.price > 0]

    pdf.ln(20)
    cover_stats = [
        (f"{len(listings):,}", "Properties"),
        (f"${_median(price_vals):,.0f}" if price_vals else "N/A", "Median Price"),
        (f"${_median(ppsf_vals):,.0f}" if ppsf_vals else "N/A", "Median $/SqFt"),
        (f"{_median(dom_vals):.0f}" if dom_vals else "N/A", "Median DOM"),
    ]

    stat_w = 40
    total_w = len(cover_stats) * stat_w + (len(cover_stats) - 1) * 6
    x_start = (pdf.w - total_w) / 2
    y_stat = pdf.get_y()

    for idx, (val, label) in enumerate(cover_stats):
        x = x_start + idx * (stat_w + 6)

        # Card bg
        pdf.set_fill_color(*BG_LIGHT)
        pdf.set_draw_color(*DIVIDER)
        pdf.set_line_width(0.3)
        pdf.rect(x, y_stat, stat_w, 22, "DF")

        # Top accent
        pdf.set_fill_color(*ACCENT_BAR)
        pdf.rect(x, y_stat, stat_w, 1.5, "F")

        pdf.set_xy(x, y_stat + 4)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(stat_w, 7, val, align="C")

        pdf.set_xy(x, y_stat + 13)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*LIGHT_TEXT)
        pdf.cell(stat_w, 4, label.upper(), align="C")

    # Scope line
    zip_groups: dict[str, list[Listing]] = {}
    for l in listings:
        z = l.zip_code or "Unknown"
        zip_groups.setdefault(z, []).append(l)
    zip_list = ", ".join(sorted(zip_groups.keys()))
    cities_in_data = sorted(set(l.city for l in listings if l.city))
    city_list = ", ".join(cities_in_data) if cities_in_data else city

    pdf.set_y(pdf.get_y() + 14)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MID_TEXT)
    pdf.cell(0, 4, f"{len(listings):,} properties  |  {zip_list}  |  {city_list}", align="C",
             new_x="LMARGIN", new_y="NEXT")

    # --- Buyer Profile ---
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*DARK_TEXT)
    buyer_label = "INVESTOR PROFILE" if is_investment else "BUYER PROFILE"
    pdf.cell(0, 6, buyer_label, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Compute estimated payment
    mortgage_rate = rates.mortgage_30yr / 100 if rates and rates.mortgage_30yr else params.interest_rate
    if target_price:
        loan_amt = target_price * (1 - params.down_pmt_pct)
        monthly_r = mortgage_rate / 12
        n_payments = 360
        if monthly_r > 0:
            est_payment = loan_amt * (monthly_r * (1 + monthly_r) ** n_payments) / (
                (1 + monthly_r) ** n_payments - 1
            )
        else:
            est_payment = loan_amt / n_payments
    else:
        est_payment = None

    # Build profile lines
    profile_lines = []
    if target_price:
        profile_lines.append(f"Target Price: ${target_price:,}")
    if target_beds:
        profile_lines.append(f"Target Beds: {target_beds}")
    if rates and rates.mortgage_30yr:
        profile_lines.append(f"Est. Rate (30yr): {rates.mortgage_30yr:.2f}%")
    if est_payment:
        profile_lines.append(f"Est. Monthly P&I: ${est_payment:,.0f}*")
    if is_investment:
        profile_lines.append(f"Rent Assumption: ${params.per_bed_rent:,.0f}/bed/mo")

    # Render 2 columns of profile items centered
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*DARK_TEXT)
    col_w = 80
    x_left = (pdf.w - col_w * 2) / 2
    for i, line in enumerate(profile_lines):
        col = i % 2
        if col == 0:
            pdf.set_x(x_left)
        else:
            pdf.set_x(x_left + col_w)
        pdf.cell(col_w, 5, line, align="L")
        if col == 1 or i == len(profile_lines) - 1:
            pdf.ln(5)

    # Asterisk footnote
    if est_payment:
        pdf.set_font("Helvetica", "I", 6.5)
        pdf.set_text_color(*LIGHT_TEXT)
        pdf.cell(0, 3,
                 f"* Assumes {params.down_pmt_pct:.0%} down payment, 30yr fixed, 740+ credit score. "
                 "Actual payment will vary by lender, credit, and terms.",
                 align="C", new_x="LMARGIN", new_y="NEXT")

    # --- Table of Contents ---
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*DARK_TEXT)
    pdf.cell(0, 6, "CONTENTS", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    toc_items = [
        ("1  Market Snapshot", "Regional metrics, rates, price tiers, closed market, commentary"),
        ("2  Neighborhood Profile", "Zip comparison, distributions, comps, area news"),
    ]

    for title, desc in toc_items:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 5, title, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*LIGHT_TEXT)
        pdf.cell(0, 4, desc, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    # Disclaimer at bottom of cover
    pdf.set_y(258)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.set_text_color(*LIGHT_TEXT)
    pdf.multi_cell(0, 3, (
        "This report is generated by RE Analytics for informational purposes only. "
        "Data sourced from public listing services. Not financial advice. "
        "Consult a licensed professional before making purchasing decisions."
    ), align="C")

    pdf.is_cover = False

    # ==================== 1. MARKET SNAPSHOT (Regional / Macro) ====================
    pdf.add_page()
    pdf.section_title("Market Snapshot")

    # Row 1: Active market metrics
    pdf.metric_cards([
        ("Active Inventory", f"{len(active):,}"),
        ("Median List Price", f"${_median(price_vals):,.0f}" if price_vals else "N/A"),
        ("Median $/SqFt", f"${_median(ppsf_vals):,.0f}" if ppsf_vals else "N/A"),
        ("Median DOM", f"{_median(dom_vals):.0f} days" if dom_vals else "N/A"),
    ])

    # Row 2: Rate metrics
    if rates and rates.mortgage_30yr:
        pdf.metric_cards([
            ("30-Yr Mortgage", f"{rates.mortgage_30yr:.2f}%"),
            ("15-Yr Mortgage", f"{rates.mortgage_15yr:.2f}%" if rates.mortgage_15yr else "N/A"),
            ("Fed Funds Rate", f"{rates.fed_funds_rate:.2f}%" if rates.fed_funds_rate else "N/A"),
            ("10-Yr Treasury", f"{rates.treasury_10yr:.2f}%" if rates.treasury_10yr else "N/A"),
        ])

        spread = rates.spread_over_treasury
        if spread:
            signal = "wider than historical avg - rates may compress" if spread > 2.0 else "near historical norms"
            pdf.callout_box(
                "Mortgage-Treasury Spread",
                f"Current spread: {spread:.2f}% ({signal}). Historical average is approx. 1.7%.",
            )

    # Home price appreciation
    if appreciation and (appreciation.get("yoy_pct") or appreciation.get("five_yr_pct")):
        yoy = appreciation.get("yoy_pct")
        fiveyr = appreciation.get("five_yr_pct")
        appr_cards = []
        if yoy is not None:
            appr_cards.append(("1-Year HPI Change", f"{yoy:+.1f}%"))
        if fiveyr is not None:
            appr_cards.append(("5-Year HPI Change", f"{fiveyr:+.1f}%"))
        # Annualized 5yr
        if fiveyr is not None:
            annualized = ((1 + fiveyr / 100) ** 0.2 - 1) * 100
            appr_cards.append(("5-Yr Annualized", f"{annualized:+.1f}%"))
        appr_cards.append(("Source", "FHFA HPI"))
        pdf.subsection_title("Home Price Appreciation (Metro)")
        pdf.metric_cards(appr_cards)
    else:
        pdf.subsection_title("Home Price Appreciation")
        pdf.callout_box(
            "Data Pending",
            "Home price appreciation data (FHFA House Price Index) requires a FRED API key. "
            "Once configured, 1-year and 5-year metro-level appreciation will display here.",
        )

    # Closed market metrics (placeholder until MLS access)
    closed = [l for l in listings if l.sold_price and l.sold_price > 0]
    if closed:
        sold_prices = [l.sold_price for l in closed]
        stl_ratios = [l.sale_to_list_ratio for l in closed if l.sale_to_list_ratio]
        below_list = [r for r in stl_ratios if r < 1.0]
        pct_below = f"{len(below_list) / len(stl_ratios) * 100:.0f}%" if stl_ratios else "N/A"
        closed_dom = [l.days_on_market for l in closed if l.days_on_market is not None]

        pdf.subsection_title("Closed Market (Recent Sales)")
        pdf.metric_cards([
            ("Closed Sales", str(len(closed))),
            ("Med. Sold Price", f"${_median(sold_prices):,.0f}"),
            ("% Below List", pct_below),
            ("Med. DOM to Close", f"{_median(closed_dom):.0f}" if closed_dom else "N/A"),
        ])
    else:
        pdf.subsection_title("Closed Market (Recent Sales)")
        pdf.callout_box(
            "MLS Data Required",
            "Closed sale prices, sale-to-list ratios, and DOM to close require MLS API access "
            "(UtahRealEstate RESO feed). Fields: ClosePrice, OriginalListPrice, CloseDate. "
            "This section will populate automatically once connected.",
        )

    # Neighborhood summaries (city-level overview for macro context)
    city_groups: dict[str, list[Listing]] = {}
    for l in listings:
        c = l.city or "Unknown"
        city_groups.setdefault(c, []).append(l)

    if len(city_groups) >= 1:
        pdf.subsection_title("Neighborhood Summaries")
        city_rows = []
        for c in sorted(city_groups.keys()):
            group = city_groups[c]
            g_prices = [l.price for l in group if l.price > 0]
            g_ppsf = [l.price_per_sqft for l in group if l.price_per_sqft]
            g_dom = [l.days_on_market for l in group if l.days_on_market is not None]
            g_closed = [l for l in group if l.sold_price and l.sold_price > 0]
            g_stl = [l.sale_to_list_ratio for l in g_closed if l.sale_to_list_ratio]
            g_below = f"{len([r for r in g_stl if r < 1.0]) / len(g_stl) * 100:.0f}%" if g_stl else "--"
            city_rows.append([
                c,
                str(len(group)),
                f"${_median(g_prices):,.0f}" if g_prices else "-",
                f"${_median(g_ppsf):,.0f}" if g_ppsf else "-",
                f"{_median(g_dom):.0f}" if g_dom else "-",
                g_below,
            ])
        pdf.styled_table(
            ["City", "Listings", "Med. List", "Med. $/SqFt", "Med. DOM", "% Below List*"],
            city_rows,
            [32, 18, 28, 26, 20, 28],
            ["L", "C", "R", "R", "C", "C"],
        )
        pdf.set_font("Helvetica", "I", 6.5)
        pdf.set_text_color(*LIGHT_TEXT)
        pdf.cell(0, 3, "* % Below List requires MLS closed sale data. '--' indicates data unavailable.",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Price tier + DOM combined table (dynamic buckets)
    pdf.subsection_title("Price Tiers & Days on Market")
    tiers = _dynamic_buckets(price_vals, num_buckets=5)
    tier_rows = []
    for label, lo, hi in tiers:
        group = [l for l in listings if lo <= l.price < hi]
        if not group:
            continue
        g_dom = [l.days_on_market for l in group if l.days_on_market is not None]
        g_ppsf = [l.price_per_sqft for l in group if l.price_per_sqft]
        g_price = [l.price for l in group if l.price > 0]
        med_d = _median(g_dom) if g_dom else None
        pace = _dom_pace(med_d)
        dom_str = f"{med_d:.0f} ({pace})" if med_d is not None else "-"
        tier_rows.append([
            label,
            str(len(group)),
            f"${_median(g_price):,.0f}" if g_price else "-",
            f"${_median(g_ppsf):,.0f}" if g_ppsf else "-",
            dom_str,
            f"{len(group) / len(listings) * 100:.0f}%" if listings else "-",
        ])
    pdf.styled_table(
        ["Price Tier", "Count", "Med. Price", "Med. $/SqFt", "Med. DOM", "Share"],
        tier_rows,
        [30, 16, 30, 28, 32, 18],
        ["L", "C", "R", "R", "C", "C"],
    )

    # Market commentary
    commentary = _build_commentary(listings, city, state, is_investment, rates)
    for title, text in commentary.items():
        if title == "Key Takeaway":
            pdf.callout_box(title, text)
        else:
            pdf.subsection_title(title)
            pdf.body_text(text)

    # ==================== 2. NEIGHBORHOOD PROFILE (Micro / Target Area) ====================
    pdf.add_page()
    pdf.section_title("Neighborhood Profile")

    # Growth context at top of neighborhood section
    if appreciation and (appreciation.get("yoy_pct") or appreciation.get("five_yr_pct")):
        yoy = appreciation.get("yoy_pct")
        fiveyr = appreciation.get("five_yr_pct")
        parts = []
        if yoy is not None:
            direction = "up" if yoy > 0 else "down"
            parts.append(f"home prices are {direction} {abs(yoy):.1f}% year-over-year")
        if fiveyr is not None:
            parts.append(f"{fiveyr:+.1f}% over 5 years")
        growth_text = (
            f"In the {city} metro area, {' and '.join(parts)} "
            f"(FHFA House Price Index). "
        )
        if yoy and yoy > 5:
            growth_text += "Strong appreciation suggests a competitive market for buyers."
        elif yoy and yoy > 0:
            growth_text += "Moderate, healthy growth indicates a stable market."
        elif yoy and yoy <= 0:
            growth_text += "Flat or declining prices may create buying opportunities."
        pdf.callout_box("Growth Trend", growth_text)

    # Zip code comparison with city
    if len(zip_groups) > 1:
        pdf.subsection_title("Zip Code Comparison")
        zip_rows = []
        for z in sorted(zip_groups.keys()):
            group = zip_groups[z]
            g_dom = [l.days_on_market for l in group if l.days_on_market is not None]
            # Get primary city for this zip
            zip_city = max(set(l.city for l in group if l.city), key=lambda c: sum(1 for l in group if l.city == c)) if any(l.city for l in group) else "-"
            zip_rows.append([
                z,
                zip_city,
                str(len(group)),
                f"${_median([l.price for l in group if l.price > 0]):,.0f}"
                    if group else "-",
                f"${_median([l.price_per_sqft for l in group if l.price_per_sqft]):,.0f}"
                    if any(l.price_per_sqft for l in group) else "-",
                f"{_median(g_dom):.0f}" if g_dom else "-",
            ])
        pdf.styled_table(
            ["Zip", "City", "Count", "Med. Price", "Med. $/SqFt", "Med. DOM"],
            zip_rows,
            [20, 28, 16, 28, 26, 20],
            ["C", "L", "C", "R", "R", "C"],
        )

    # Price distribution (dynamic buckets)
    pdf.subsection_title("Price Distribution")
    price_bands = _dynamic_buckets(price_vals, num_buckets=5)
    dist_rows = []
    for label, lo, hi in price_bands:
        count = len([l for l in listings if lo <= l.price < hi])
        pct = f"{count / len(listings) * 100:.0f}%" if listings else "0%"
        bar = "#" * min(int(count / max(len(listings), 1) * 25), 25)
        dist_rows.append([label, str(count), pct, bar])
    pdf.styled_table(
        ["Price Band", "Count", "Share", ""],
        dist_rows,
        [30, 16, 16, 100],
        ["L", "C", "C", "L"],
    )

    # $/SqFt distribution (dynamic buckets)
    if ppsf_vals:
        pdf.subsection_title("Price per SqFt Distribution")
        ppsf_bands = _dynamic_buckets(ppsf_vals, num_buckets=5)
        ppsf_rows = []
        for label, lo_b, hi_b in ppsf_bands:
            count = len([l for l in listings if l.price_per_sqft and lo_b <= l.price_per_sqft < hi_b])
            if count > 0:
                pct = f"{count / len(listings) * 100:.0f}%"
                bar = "#" * min(int(count / max(len(listings), 1) * 25), 25)
                ppsf_rows.append([label, str(count), pct, bar])
        if ppsf_rows:
            pdf.styled_table(
                ["$/SqFt Range", "Count", "Share", ""],
                ppsf_rows,
                [30, 16, 16, 100],
                ["L", "C", "C", "L"],
            )

    # --- Comparable Analysis (within neighborhood section) ---
    if target_price:
        pdf.subsection_title("Comparable Analysis")

        price_lo = int(target_price * 0.85)
        price_hi = int(target_price * 1.15)
        comps = [l for l in listings if price_lo <= l.price <= price_hi]
        if target_beds:
            comps_beds = [l for l in comps if l.bedrooms and abs(l.bedrooms - target_beds) <= 1]
            if len(comps_beds) >= 3:
                comps = comps_beds

        pdf.callout_box(
            "Comp Criteria",
            f"Target: ${target_price:,} (+/-15% = ${price_lo:,}-${price_hi:,})"
            + (f", {target_beds} beds (+/-1)" if target_beds else "")
            + f". Found {len(comps)} comparable properties.",
        )

        if comps:
            # Market position
            all_prices = sorted([l.price for l in listings if l.price > 0])
            if all_prices:
                below = len([p for p in all_prices if p <= target_price])
                percentile = int(below / len(all_prices) * 100)
                pdf.body_text(
                    f"A ${target_price:,} property sits at the {percentile}th percentile "
                    f"of {len(all_prices)} listings. "
                    f"Range: ${min(all_prices):,}-${max(all_prices):,}, "
                    f"median ${_median(all_prices):,.0f}."
                )

            # Comp stats
            comp_prices = [l.price for l in comps]
            comp_ppsf = [l.price_per_sqft for l in comps if l.price_per_sqft]
            comp_dom = [l.days_on_market for l in comps if l.days_on_market is not None]
            comp_sold = [l for l in comps if l.sold_price and l.sold_price > 0]

            cards = [
                ("Comps Found", str(len(comps))),
                ("Med. List Price", f"${_median(comp_prices):,.0f}" if comp_prices else "N/A"),
                ("Med. $/SqFt", f"${_median(comp_ppsf):,.0f}" if comp_ppsf else "N/A"),
                ("Med. DOM", f"{_median(comp_dom):.0f}" if comp_dom else "N/A"),
            ]
            pdf.metric_cards(cards)

            # Comp table with status, list + sold price
            comp_headers = ["Status", "Address", "List", "Sold*", "Beds", "SqFt", "$/SqFt", "DOM"]
            comp_widths = [16, 38, 20, 20, 12, 16, 18, 12]
            comp_aligns = ["C", "L", "R", "R", "C", "R", "R", "C"]
            comp_rows = []
            comp_links = []

            # Sort: sold first (most useful for comps), then active, by price proximity
            def _comp_sort_key(l):
                is_sold = 0 if (l.sold_price and l.sold_price > 0) else 1
                return (is_sold, abs(l.price - target_price))

            for l in sorted(comps, key=_comp_sort_key)[:20]:
                sold_str = f"${l.sold_price:,}" if l.sold_price else "--"
                status = _normalize_status(l)
                comp_rows.append([
                    status,
                    l.address[:22],
                    f"${l.price:,}",
                    sold_str,
                    str(l.bedrooms or "-"),
                    f"{l.sqft:,}" if l.sqft else "-",
                    f"${l.price_per_sqft:,.0f}" if l.price_per_sqft else "-",
                    f"{l.days_on_market:.0f}" if l.days_on_market else "-",
                ])
                comp_links.append(l.listing_url or "")
            pdf.styled_table(comp_headers, comp_rows, comp_widths, comp_aligns, links=comp_links)

            # Counts by status
            n_sold = sum(1 for r in comp_rows if r[0] == "Sold")
            n_active = sum(1 for r in comp_rows if r[0] == "Active")
            n_pending = sum(1 for r in comp_rows if r[0] == "Pending")
            status_parts = []
            if n_sold:
                status_parts.append(f"{n_sold} sold")
            if n_active:
                status_parts.append(f"{n_active} active")
            if n_pending:
                status_parts.append(f"{n_pending} pending")
            status_summary = ", ".join(status_parts) if status_parts else ""

            pdf.set_font("Helvetica", "I", 6.5)
            pdf.set_text_color(*LIGHT_TEXT)
            footnote = "* Sold price requires MLS closed data. '--' = pending or unavailable."
            if status_summary:
                footnote += f"  ({status_summary})"
            pdf.cell(0, 3, footnote, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

            # Value assessment
            if comp_ppsf and comps:
                avg_sqft = _median([l.sqft for l in comps if l.sqft])
                med_comp_ppsf = _median(comp_ppsf)
                if avg_sqft and med_comp_ppsf:
                    implied_value = int(avg_sqft * med_comp_ppsf)
                    diff = target_price - implied_value
                    if diff > 0:
                        assessment = f"${diff:,} above implied value - negotiate or verify premium features."
                    elif diff < 0:
                        assessment = f"${abs(diff):,} below implied value - potential upside."
                    else:
                        assessment = "Right at implied market value."
                    pdf.callout_box(
                        "Value Assessment",
                        f"Median comp $/sqft: ${med_comp_ppsf:,.0f}, median comp size: "
                        f"{avg_sqft:,.0f} sqft. Implied value: ${implied_value:,}. "
                        f"Target at ${target_price:,} is {assessment}",
                    )
        else:
            pdf.body_text(
                "No comparable properties found within criteria. "
                "Consider widening the price range or adjusting bed count."
            )

    # --- Recent Area News ---
    pdf.subsection_title("Recent Area News & Developments")
    if area_news:
        pdf.body_text(area_news)
    else:
        pdf.callout_box(
            "News Unavailable",
            f"Area news for {city}, {state} requires an Anthropic API key (ANTHROPIC_API_KEY). "
            "When configured, this section auto-populates with recent (T12-24mo) real estate "
            "and community developments: zoning changes, employer activity, infrastructure, "
            "and demographic trends.",
        )

    return pdf.output()
