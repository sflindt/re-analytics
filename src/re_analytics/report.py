"""PDF report generation for RE Analytics - professional branded design."""

from __future__ import annotations

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
    ):
        """Render a professional table with dark header and alternating rows."""
        if not headers:
            return
        margin = 18
        available = self.w - 2 * margin
        if col_widths is None:
            col_widths = [available / len(headers)] * len(headers)

        if align is None:
            align = ["C"] * len(headers)

        row_h = 6

        # Header
        self.set_font("Helvetica", "B", 7.5)
        self.set_fill_color(*BG_TABLE_HEADER)
        self.set_text_color(*WHITE)
        self.set_draw_color(*BG_TABLE_HEADER)
        self.set_line_width(0.1)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], row_h + 1, h, border=0, fill=True, align="C")
        self.ln()

        # Rows
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*DARK_TEXT)
        for r_idx, row in enumerate(rows):
            if self.get_y() > self.h - 30:
                self.add_page()
                # Re-draw header on new page
                self.set_font("Helvetica", "B", 7.5)
                self.set_fill_color(*BG_TABLE_HEADER)
                self.set_text_color(*WHITE)
                for i, h in enumerate(headers):
                    self.cell(col_widths[i], row_h + 1, h, border=0, fill=True, align="C")
                self.ln()
                self.set_font("Helvetica", "", 7.5)
                self.set_text_color(*DARK_TEXT)

            # Alternating row background
            if r_idx % 2 == 1:
                self.set_fill_color(*BG_ROW_ALT)
                fill = True
            else:
                self.set_fill_color(*WHITE)
                fill = True

            for i, val in enumerate(row):
                self.cell(col_widths[i], row_h, str(val), border=0, fill=fill, align=align[i])
            self.ln()

        # Bottom border
        self.set_draw_color(*DIVIDER)
        self.set_line_width(0.3)
        self.line(margin, self.get_y(), margin + sum(col_widths), self.get_y())
        self.ln(4)

    def styled_table_with_links(
        self,
        headers: list[str],
        rows: list[list[str]],
        links: list[str],
        col_widths: list[float] | None = None,
        align: list[str] | None = None,
    ):
        """Table with a clickable 'View' link column appended at the end."""
        if not headers:
            return
        margin = 18
        link_col_w = 18
        available = self.w - 2 * margin
        if col_widths is None:
            data_w = available - link_col_w
            col_widths = [data_w / len(headers)] * len(headers)

        if align is None:
            align = ["C"] * len(headers)

        all_headers = headers + ["Link"]
        all_widths = list(col_widths) + [link_col_w]
        all_aligns = list(align) + ["C"]

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

            if r_idx % 2 == 1:
                self.set_fill_color(*BG_ROW_ALT)
            else:
                self.set_fill_color(*WHITE)

            for i, val in enumerate(row):
                self.cell(all_widths[i], row_h, str(val), border=0, fill=True, align=all_aligns[i])

            # Link cell
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
        x = margin
        y = self.get_y()

        # Measure text height
        self.set_font("Helvetica", "", 8.5)
        line_h = 4.5
        # Estimate lines
        lines = len(text) / (box_w / 2.2) + 1
        box_h = max(18, 8 + lines * line_h)

        # Background
        self.set_fill_color(*BG_LIGHT)
        self.rect(x, y, box_w, box_h, "F")

        # Left accent bar
        self.set_fill_color(*ACCENT_BAR)
        self.rect(x, y, 3, box_h, "F")

        # Title
        self.set_xy(x + 7, y + 3)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*BLUE)
        self.cell(box_w - 14, 5, title, align="L")

        # Text
        self.set_xy(x + 7, y + 9)
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(*DARK_TEXT)
        self.multi_cell(box_w - 14, line_h, text)

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
            macro = (
                f"The Federal Reserve is maintaining a restrictive monetary policy with the "
                f"federal funds rate at {rates.fed_funds_rate:.2f}%. The 30-year fixed mortgage "
                f"rate stands at {rates.mortgage_30yr:.2f}%, reflecting elevated borrowing costs. "
                f"If inflation continues to moderate, rate cuts over the next 12-24 months could "
                f"improve affordability and boost transaction volume."
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
                f"Lower rates support stronger property valuations and buyer purchasing power."
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
                "Buyers have time and negotiating room. Use longer selling times as leverage."
            )
        else:
            sections["Key Takeaway"] = (
                "Get pre-approved and know your budget ceiling. Compare options across "
                "neighborhoods and price points to find the best value."
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
    pdf.cell(0, 8, "Real Estate Market Intelligence", align="C", new_x="LMARGIN", new_y="NEXT")

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

    # Scope + Table of Contents on cover page
    zip_groups: dict[str, list[Listing]] = {}
    for l in listings:
        z = l.zip_code or "Unknown"
        zip_groups.setdefault(z, []).append(l)
    zip_list = ", ".join(sorted(zip_groups.keys()))
    cities_in_data = sorted(set(l.city for l in listings if l.city))
    city_list = ", ".join(cities_in_data) if cities_in_data else city

    pdf.set_y(pdf.get_y() + 18)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MID_TEXT)
    pdf.cell(0, 4, f"{len(listings):,} properties  |  {zip_list}  |  {city_list}", align="C",
             new_x="LMARGIN", new_y="NEXT")

    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*DARK_TEXT)
    pdf.cell(0, 6, "CONTENTS", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    toc_items = [
        ("1  Market Snapshot", "Metrics, rates, price tiers, commentary"),
        ("2  Neighborhood Profile", "Zip comparison and distributions"),
    ]
    if target_price:
        toc_items.append(
            ("3  Comparable Analysis",
             f"Comps near ${target_price:,}" + (f", {target_beds} beds" if target_beds else "")),
        )

    for title, desc in toc_items:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 5, title, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*LIGHT_TEXT)
        pdf.cell(0, 4, desc, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    # Disclaimer at bottom of cover
    pdf.set_y(255)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.set_text_color(*LIGHT_TEXT)
    pdf.multi_cell(0, 3, (
        "This report is generated by RE Analytics for informational purposes only. "
        "Data sourced from public listing services. Not financial advice. "
        "Consult a licensed professional before making purchasing decisions."
    ), align="C")

    pdf.is_cover = False

    # ==================== 1. MARKET SNAPSHOT ====================
    pdf.add_page()
    pdf.section_title("Market Snapshot")

    # Row 1: Market metrics
    pdf.metric_cards([
        ("Active Inventory", f"{len(active):,}"),
        ("Median Price", f"${_median(price_vals):,.0f}" if price_vals else "N/A"),
        ("Median $/SqFt", f"${_median(ppsf_vals):,.0f}" if ppsf_vals else "N/A"),
        ("Median DOM", f"{_median(dom_vals):.0f} days" if dom_vals else "N/A"),
    ])

    # Row 2: Rate metrics (inline, no separate section)
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

    # Price tier + DOM combined table
    pdf.subsection_title("Price Tiers & Days on Market")
    tiers = [
        ("Under $300K", 0, 300_000),
        ("$300K-$500K", 300_000, 500_000),
        ("$500K-$750K", 500_000, 750_000),
        ("$750K-$1M", 750_000, 1_000_000),
        ("$1M+", 1_000_000, 999_999_999),
    ]
    tier_rows = []
    for label, lo, hi in tiers:
        group = [l for l in listings if lo <= l.price < hi]
        if not group:
            tier_rows.append([label, "0", "-", "-", "-", "-"])
            continue
        g_dom = [l.days_on_market for l in group if l.days_on_market is not None]
        g_ppsf = [l.price_per_sqft for l in group if l.price_per_sqft]
        g_price = [l.price for l in group if l.price > 0]
        # DOM speed indicator
        med_d = _median(g_dom) if g_dom else None
        if med_d is not None:
            if med_d < 14:
                pace = "Fast"
            elif med_d < 30:
                pace = "Normal"
            elif med_d < 60:
                pace = "Slow"
            else:
                pace = "Stale"
            dom_str = f"{med_d:.0f} ({pace})"
        else:
            dom_str = "-"
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

    # Market commentary (inline, no new page)
    commentary = _build_commentary(listings, city, state, is_investment, rates)
    for title, text in commentary.items():
        if title == "Key Takeaway":
            pdf.callout_box(title, text)
        else:
            pdf.subsection_title(title)
            pdf.body_text(text)

    # ==================== 2. NEIGHBORHOOD PROFILE ====================
    pdf.add_page()
    pdf.section_title("Neighborhood Profile")

    # Neighborhood comparison table
    if len(zip_groups) > 1:
        pdf.subsection_title("Zip Code Comparison")
        zip_rows = []
        for z in sorted(zip_groups.keys()):
            group = zip_groups[z]
            g_dom = [l.days_on_market for l in group if l.days_on_market is not None]
            zip_rows.append([
                z,
                str(len(group)),
                f"${_median([l.price for l in group if l.price > 0]):,.0f}"
                    if group else "-",
                f"${_median([l.price_per_sqft for l in group if l.price_per_sqft]):,.0f}"
                    if any(l.price_per_sqft for l in group) else "-",
                f"{_median(g_dom):.0f}" if g_dom else "-",
                f"${min(l.price for l in group):,}-${max(l.price for l in group):,}",
            ])
        pdf.styled_table(
            ["Zip", "Count", "Med. Price", "Med. $/SqFt", "Med. DOM", "Price Range"],
            zip_rows,
            [20, 16, 30, 28, 20, 48],
            ["C", "C", "R", "R", "C", "C"],
        )

    # Price distribution (compact)
    pdf.subsection_title("Price Distribution")
    price_bands = [
        ("Under $400K", 0, 400_000),
        ("$400K-$500K", 400_000, 500_000),
        ("$500K-$600K", 500_000, 600_000),
        ("$600K-$750K", 600_000, 750_000),
        ("$750K+", 750_000, 999_999_999),
    ]
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

    # $/SqFt distribution (compact, side by side concept - just fewer bands)
    if ppsf_vals:
        pdf.subsection_title("Price per SqFt Distribution")
        min_ppsf = int(min(ppsf_vals))
        max_ppsf = int(max(ppsf_vals))
        step = max(50, (max_ppsf - min_ppsf) // 5)
        ppsf_bands = []
        lo = (min_ppsf // step) * step
        while lo < max_ppsf + step:
            hi = lo + step
            ppsf_bands.append((f"${lo}-${hi}", lo, hi))
            lo = hi
        ppsf_rows = []
        for label, lo, hi in ppsf_bands:
            count = len([l for l in listings if l.price_per_sqft and lo <= l.price_per_sqft < hi])
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

    # ==================== 3. COMPARABLE ANALYSIS ====================
    if target_price:
        pdf.add_page()
        pdf.section_title("Comparable Analysis")

        # Filter comps: within +/-15% of target price, matching beds if specified
        price_lo = int(target_price * 0.85)
        price_hi = int(target_price * 1.15)
        comps = [l for l in listings if price_lo <= l.price <= price_hi]
        if target_beds:
            comps_beds = [l for l in comps if l.bedrooms and abs(l.bedrooms - target_beds) <= 1]
            if len(comps_beds) >= 3:
                comps = comps_beds

        pdf.callout_box(
            "Comp Criteria",
            f"Target price: ${target_price:,} (+/-15% = ${price_lo:,} - ${price_hi:,})"
            + (f", {target_beds} beds (+/-1)" if target_beds else "")
            + f". Found {len(comps)} comparable properties.",
        )

        if comps:
            # Where does target sit in the market?
            all_prices = sorted([l.price for l in listings if l.price > 0])
            if all_prices:
                below = len([p for p in all_prices if p <= target_price])
                percentile = int(below / len(all_prices) * 100)
                pdf.subsection_title("Market Position")
                pdf.body_text(
                    f"A ${target_price:,} property sits at the {percentile}th percentile "
                    f"of the market (out of {len(all_prices)} properties). "
                    f"Market range: ${min(all_prices):,} - ${max(all_prices):,}, "
                    f"median ${_median(all_prices):,.0f}."
                )

            # Comp summary stats
            comp_prices = [l.price for l in comps]
            comp_ppsf = [l.price_per_sqft for l in comps if l.price_per_sqft]
            comp_dom = [l.days_on_market for l in comps if l.days_on_market is not None]

            pdf.metric_cards([
                ("Comps Found", str(len(comps))),
                ("Med. Comp Price", f"${_median(comp_prices):,.0f}" if comp_prices else "N/A"),
                ("Med. Comp $/SqFt", f"${_median(comp_ppsf):,.0f}" if comp_ppsf else "N/A"),
                ("Med. Comp DOM", f"{_median(comp_dom):.0f} days" if comp_dom else "N/A"),
            ])

            # Comp listings table with clickable links
            pdf.subsection_title("Comparable Properties")
            comp_headers = ["Address", "Price", "Beds", "SqFt", "$/SqFt", "DOM", "Zip"]
            comp_widths = [48, 24, 14, 20, 22, 14, 20]
            comp_aligns = ["L", "R", "C", "R", "R", "C", "C"]
            comp_rows = []
            comp_links = []
            for l in sorted(comps, key=lambda x: abs(x.price - target_price))[:20]:
                comp_rows.append([
                    l.address[:26],
                    f"${l.price:,}",
                    str(l.bedrooms or "-"),
                    f"{l.sqft:,}" if l.sqft else "-",
                    f"${l.price_per_sqft:,.0f}" if l.price_per_sqft else "-",
                    f"{l.days_on_market:.0f}" if l.days_on_market else "-",
                    l.zip_code or "-",
                ])
                comp_links.append(l.listing_url or "")
            pdf.styled_table_with_links(comp_headers, comp_rows, comp_links, comp_widths, comp_aligns)

            # Value assessment
            if comp_ppsf and target_price and comps:
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
                        f"Based on median comp $/sqft of ${med_comp_ppsf:,.0f} and "
                        f"median comp size of {avg_sqft:,.0f} sqft, implied value is "
                        f"${implied_value:,}. Target at ${target_price:,} is {assessment}",
                    )
        else:
            pdf.body_text(
                "No comparable properties found within the specified criteria. "
                "Consider widening the price range or adjusting bed count."
            )

    return pdf.output()
