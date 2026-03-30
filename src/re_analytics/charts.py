"""Matplotlib chart generators for PDF report embedding.

Each function returns a BytesIO PNG buffer ready for pdf.image().
Uses the report's brand palette for visual consistency.
"""

from __future__ import annotations

import io
from typing import Sequence

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# Brand palette (matching report.py)
NAVY = "#1B2A4A"
BLUE = "#2E5090"
LIGHT_BLUE = "#4A90D9"
ACCENT_GREEN = "#27AE60"
ACCENT_RED = "#C0392B"
ACCENT_ORANGE = "#E67E22"
LIGHT_GRAY = "#F5F6F8"
MID_GRAY = "#BDC3C7"
DARK_TEXT = "#2C3E50"

# Consistent chart styling
CHART_DPI = 150
FONT_SIZE = 8
TITLE_SIZE = 10


def _setup_style():
    """Apply consistent chart styling."""
    plt.rcParams.update({
        "font.size": FONT_SIZE,
        "font.family": "sans-serif",
        "axes.titlesize": TITLE_SIZE,
        "axes.titleweight": "bold",
        "axes.labelsize": FONT_SIZE,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.facecolor": "white",
        "figure.facecolor": "white",
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
    })


def _compact_price(x, _=None) -> str:
    """Format price as compact string: $1.3M, $450K, etc."""
    if x >= 1_000_000:
        return f"${x / 1_000_000:.1f}M"
    elif x >= 1_000:
        return f"${x / 1_000:.0f}K"
    return f"${x:,.0f}"


def _fig_to_bytes(fig) -> io.BytesIO:
    """Render figure to PNG BytesIO buffer."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight",
                facecolor="white", edgecolor="none", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_distribution(
    bands: list[tuple[str, float, float]],
    values: list[float],
    xlabel: str = "Range",
    title: str = "Distribution",
    color: str = BLUE,
    figsize: tuple[float, float] = (3.5, 1.8),
) -> io.BytesIO:
    """Bar chart for price or $/sqft distribution.

    Args:
        bands: List of (label, lo, hi) tuples from _dynamic_buckets
        values: Raw values to bin into bands
        xlabel: X-axis label
        title: Chart title
    """
    _setup_style()
    labels = []
    counts = []
    for label, lo, hi in bands:
        count = len([v for v in values if lo <= v < hi])
        if count > 0 or len(bands) <= 8:
            labels.append(label)
            counts.append(count)

    if not counts:
        return io.BytesIO()

    fig, ax = plt.subplots(figsize=figsize)
    total = sum(counts)
    bars = ax.bar(range(len(labels)), counts, color=color, edgecolor="white",
                  linewidth=0.5, width=0.7)

    # Add count + percentage labels on bars
    for bar, count in zip(bars, counts):
        pct = count / total * 100 if total > 0 else 0
        if pct >= 5:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    f"{count}\n({pct:.0f}%)", ha="center", va="bottom",
                    fontsize=6, color=DARK_TEXT)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=6)
    ax.set_ylabel("# of Listings (Active)", fontsize=7)
    ax.set_title(title, fontsize=TITLE_SIZE, color=NAVY, pad=8)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    return _fig_to_bytes(fig)


def chart_price_tiers(
    tiers: list[dict],
    figsize: tuple[float, float] = (4.8, 2.1),
) -> io.BytesIO:
    """Combo bar+line chart: listing count per tier (bars) + median DOM (line).

    Args:
        tiers: List of dicts with keys: 'label', 'count', 'median_dom', 'median_ppsf'
    """
    _setup_style()
    if not tiers:
        return io.BytesIO()

    labels = [t["label"] for t in tiers]
    counts = [t["count"] for t in tiers]
    doms = [t.get("median_dom") for t in tiers]

    fig, ax1 = plt.subplots(figsize=figsize)
    x = range(len(labels))

    # Bars for count
    bars = ax1.bar(x, counts, color=BLUE, edgecolor="white", linewidth=0.5,
                   width=0.6, label="Listings", zorder=2)
    ax1.set_ylabel("# of Listings (Active)", color=BLUE, fontsize=7)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=30, ha="right", fontsize=6)
    ax1.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    # Count labels on bars
    for bar, count in zip(bars, counts):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                 str(count), ha="center", va="bottom", fontsize=6, color=BLUE)

    # Line for DOM on secondary axis
    valid_dom = [(i, d) for i, d in enumerate(doms) if d is not None]
    if valid_dom:
        ax2 = ax1.twinx()
        dom_x = [v[0] for v in valid_dom]
        dom_y = [v[1] for v in valid_dom]
        ax2.plot(dom_x, dom_y, color=ACCENT_ORANGE, marker="o", markersize=5,
                 linewidth=2, label="Med. DOM", zorder=3)
        ax2.set_ylabel("Median DOM (days)", color=ACCENT_ORANGE, fontsize=7)
        for dx, dy in zip(dom_x, dom_y):
            ax2.annotate(f"{dy:.0f}d", (dx, dy), textcoords="offset points",
                         xytext=(0, 8), ha="center", fontsize=6, color=ACCENT_ORANGE)

    ax1.set_title("Price Tiers: Inventory & Pace", fontsize=TITLE_SIZE, color=NAVY, pad=8)
    return _fig_to_bytes(fig)


def chart_zip_comparison(
    zip_data: list[dict],
    figsize: tuple[float, float] = (4.8, 2.1),
) -> io.BytesIO:
    """Horizontal bar chart comparing zip codes by median price.

    Args:
        zip_data: List of dicts with 'zip', 'city', 'median_price', 'count'
    """
    _setup_style()
    data = [d for d in zip_data if d.get("median_price")]
    if not data:
        return io.BytesIO()

    # Sort by price descending (top = highest)
    data.sort(key=lambda d: d["median_price"])
    labels = [f"{d['zip']} ({d.get('city', '')})" for d in data]
    prices = [d["median_price"] for d in data]

    fig, ax = plt.subplots(figsize=figsize)
    bars = ax.barh(range(len(labels)), prices, color=BLUE, edgecolor="white",
                   height=0.6)

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=6)
    ax.set_xlabel("Median Price", fontsize=7)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_compact_price))
    ax.tick_params(axis="x", labelsize=6)
    ax.set_title("Median Price by Zip Code", fontsize=TITLE_SIZE, color=NAVY, pad=8)

    # Compact price labels on bars
    for bar, price in zip(bars, prices):
        ax.text(bar.get_width() + max(prices) * 0.01,
                bar.get_y() + bar.get_height() / 2,
                _compact_price(price), ha="left", va="center", fontsize=6, color=DARK_TEXT)

    return _fig_to_bytes(fig)


def chart_comp_scatter(
    comps: list[dict],
    target_price: int | None = None,
    figsize: tuple[float, float] = (4.8, 2.4),
) -> io.BytesIO:
    """Scatter plot of comps: sqft vs price, colored by status.

    Args:
        comps: List of dicts with 'sqft', 'price', 'status', 'address'
        target_price: Optional horizontal target line
    """
    _setup_style()
    # Filter to comps with both sqft and price
    valid = [c for c in comps if c.get("sqft") and c.get("price")]
    if len(valid) < 2:
        return io.BytesIO()

    status_colors = {
        "Active": LIGHT_BLUE,
        "Sold": ACCENT_GREEN,
        "Pending": ACCENT_ORANGE,
    }

    fig, ax = plt.subplots(figsize=figsize)

    for status, color in status_colors.items():
        group = [c for c in valid if c.get("status") == status]
        if group:
            ax.scatter(
                [c["sqft"] for c in group],
                [c["price"] for c in group],
                c=color, s=40, label=status, edgecolors="white",
                linewidth=0.5, zorder=3, alpha=0.85,
            )

    # Catch-all for unknown status
    other = [c for c in valid if c.get("status") not in status_colors]
    if other:
        ax.scatter(
            [c["sqft"] for c in other],
            [c["price"] for c in other],
            c=MID_GRAY, s=40, label="Other", edgecolors="white",
            linewidth=0.5, zorder=2,
        )

    if target_price:
        ax.axhline(y=target_price, color=ACCENT_RED, linestyle="--", linewidth=1.2,
                    label=f"Target ${target_price:,}", zorder=1)

    ax.set_xlabel("Square Feet", fontsize=7)
    ax.set_ylabel("Price", fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_compact_price))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.tick_params(axis="both", labelsize=6)
    ax.grid(axis="both", alpha=0.2, linewidth=0.5)
    ax.set_title("Comparable Properties: Price vs Size", fontsize=TITLE_SIZE, color=NAVY, pad=8)
    ax.legend(fontsize=6, loc="upper left", framealpha=0.8)
    fig.subplots_adjust(bottom=0.18)

    return _fig_to_bytes(fig)


def chart_appreciation(
    yoy: float | None,
    five_yr: float | None,
    annualized: float | None = None,
    figsize: tuple[float, float] = (3.2, 1.6),
) -> io.BytesIO:
    """Horizontal bar chart for appreciation percentages."""
    _setup_style()
    labels = []
    values = []
    colors = []

    if yoy is not None:
        labels.append("1-Year")
        values.append(yoy)
        colors.append(ACCENT_GREEN if yoy > 0 else ACCENT_RED)
    if five_yr is not None:
        labels.append("5-Year Total")
        values.append(five_yr)
        colors.append(ACCENT_GREEN if five_yr > 0 else ACCENT_RED)
    if annualized is not None:
        labels.append("5-Year Ann.")
        values.append(annualized)
        colors.append(ACCENT_GREEN if annualized > 0 else ACCENT_RED)

    if not labels:
        return io.BytesIO()

    fig, ax = plt.subplots(figsize=figsize)
    bars = ax.barh(range(len(labels)), values, color=colors, edgecolor="white",
                   height=0.5)

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("% Change", fontsize=7)
    ax.axvline(x=0, color=MID_GRAY, linewidth=0.5, zorder=0)
    ax.set_title("Home Price Appreciation", fontsize=TITLE_SIZE, color=NAVY, pad=6)

    for bar, val in zip(bars, values):
        x_pos = bar.get_width() + (max(abs(v) for v in values) * 0.05 if val >= 0 else -max(abs(v) for v in values) * 0.05)
        ax.text(x_pos, bar.get_y() + bar.get_height() / 2,
                f"{val:+.1f}%", ha="left" if val >= 0 else "right",
                va="center", fontsize=7, fontweight="bold", color=DARK_TEXT)

    return _fig_to_bytes(fig)
