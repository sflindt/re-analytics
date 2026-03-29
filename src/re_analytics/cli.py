"""CLI entry point for re-analytics."""

from __future__ import annotations

import asyncio
import logging
import statistics
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from re_analytics.models import (
    InvestmentParams,
    Listing,
    PropertyType,
    SearchCriteria,
    listings_to_csv,
)
from re_analytics.listing_finder import find_listings

app = typer.Typer(
    name="re-analytics",
    help="Real estate investment property finder & market analytics.",
)
console = Console()


def _setup_logging(debug: bool, log: str | None) -> None:
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    handlers: list[logging.Handler] = [stream_handler]
    if log:
        file_handler = logging.FileHandler(log, mode="w")
        file_handler.setLevel(logging.DEBUG)
        handlers.append(file_handler)
    logging.basicConfig(
        level=logging.DEBUG if (debug or log) else logging.WARNING,
        format="%(name)s: %(message)s",
        handlers=handlers,
    )


def _property_type_prompt() -> PropertyType:
    console.print("\nProperty type:")
    console.print("  1. Multi-family [dim](default)[/dim]")
    console.print("  2. Single family")
    console.print("  3. Any")
    choice = typer.prompt("Choose", default="1")
    return {
        "1": PropertyType.MULTI_FAMILY,
        "2": PropertyType.SINGLE_FAMILY,
        "3": PropertyType.ANY,
    }.get(choice, PropertyType.MULTI_FAMILY)


def _format_price(price: int) -> str:
    return f"${price:,}"


def _format_optional(val, fmt: str = "{}") -> str:
    if val is None:
        return "—"
    return fmt.format(val)


def _build_table(listings: list[Listing], params: InvestmentParams) -> Table:
    table = Table(show_lines=False, pad_edge=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Address", min_width=25)
    table.add_column("Price", justify="right")
    table.add_column("SqFt", justify="right")
    table.add_column("Bed", justify="center")
    table.add_column("Bath", justify="center")
    table.add_column("$/SqFt", justify="right")
    table.add_column("RentMult", justify="right")
    table.add_column("PITI", justify="right")
    table.add_column("URL", style="dim", max_width=50, overflow="ellipsis")

    for i, l in enumerate(listings, 1):
        rent_mult = l.rent_multiplier(params.per_bed_rent)
        piti = l.monthly_piti(params)
        table.add_row(
            str(i),
            f"{l.address}\n[dim]{l.city}, {l.state} {l.zip_code}[/dim]",
            _format_price(l.price),
            _format_optional(l.sqft, "{:,}"),
            _format_optional(l.bedrooms),
            _format_optional(l.bathrooms),
            _format_optional(l.price_per_sqft, "${:,.0f}"),
            _format_optional(rent_mult, "{:,.0f}"),
            f"${piti:,.0f}",
            l.listing_url or "—",
        )
    return table


# --- Market analytics helpers ---

def _median(values: list[float | int]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def _bucket_by_price(listings: list[Listing]) -> dict[str, list[Listing]]:
    buckets: dict[str, list[Listing]] = {
        "Under $300K": [],
        "$300K-$500K": [],
        "$500K-$1M": [],
        "$1M+": [],
    }
    for l in listings:
        if l.price < 300_000:
            buckets["Under $300K"].append(l)
        elif l.price < 500_000:
            buckets["$300K-$500K"].append(l)
        elif l.price < 1_000_000:
            buckets["$500K-$1M"].append(l)
        else:
            buckets["$1M+"].append(l)
    return buckets


def _build_market_table(listings: list[Listing]) -> Table:
    """Build a market summary table with stats by price tier."""
    buckets = _bucket_by_price(listings)

    table = Table(title="Market Summary by Price Tier", show_lines=True)
    table.add_column("Price Tier", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Med. DOM", justify="right")
    table.add_column("Med. $/SqFt", justify="right")
    table.add_column("Med. Price", justify="right")
    table.add_column("Med. SqFt", justify="right")
    table.add_column("Sale/List", justify="right")

    for tier, group in buckets.items():
        if not group:
            table.add_row(tier, "0", "—", "—", "—", "—", "—")
            continue

        dom_vals = [l.days_on_market for l in group if l.days_on_market is not None]
        ppsf_vals = [l.price_per_sqft for l in group if l.price_per_sqft is not None]
        price_vals = [l.price for l in group if l.price > 0]
        sqft_vals = [l.sqft for l in group if l.sqft is not None]
        stl_vals = [l.sale_to_list_ratio for l in group if l.sale_to_list_ratio is not None]

        table.add_row(
            tier,
            str(len(group)),
            f"{_median(dom_vals):.0f}" if dom_vals else "—",
            f"${_median(ppsf_vals):,.0f}" if ppsf_vals else "—",
            f"${_median(price_vals):,.0f}" if price_vals else "—",
            f"{_median(sqft_vals):,.0f}" if sqft_vals else "—",
            f"{_median(stl_vals):.1%}" if stl_vals else "—",
        )

    return table


def _build_overview_panel(listings: list[Listing], city: str, state: str) -> Panel:
    """Build a high-level market overview panel."""
    active = [l for l in listings if l.status in ("Active", "FOR_SALE", None)]
    sold = [l for l in listings if l.status in ("Sold", "RECENTLY_SOLD")]

    dom_vals = [l.days_on_market for l in active if l.days_on_market is not None]
    ppsf_active = [l.price_per_sqft for l in active if l.price_per_sqft is not None]
    ppsf_sold = [l.price_per_sqft for l in sold if l.price_per_sqft is not None]
    stl_vals = [l.sale_to_list_ratio for l in sold if l.sale_to_list_ratio is not None]

    lines = [
        f"[bold]{city}, {state}[/bold]",
        "",
        f"Active Inventory:    [cyan]{len(active)}[/cyan] listings",
        f"Recently Sold:       [cyan]{len(sold)}[/cyan] listings",
        f"Median Days on Mkt:  [cyan]{_median(dom_vals):.0f}[/cyan]" if dom_vals else "Median Days on Mkt:  —",
        f"Median $/SqFt (Act): [cyan]${_median(ppsf_active):,.0f}[/cyan]" if ppsf_active else "Median $/SqFt (Act): —",
        f"Median $/SqFt (Sld): [cyan]${_median(ppsf_sold):,.0f}[/cyan]" if ppsf_sold else "Median $/SqFt (Sld): —",
        f"Median Sale/List:    [cyan]{_median(stl_vals):.1%}[/cyan]" if stl_vals else "Median Sale/List:    —",
    ]
    return Panel("\n".join(lines), title="Market Overview", border_style="green")


# --- CLI Commands ---

@app.callback(invoke_without_command=True)
def listings(
    ctx: typer.Context,
    city: str = typer.Option(None, "--city", "-c", help="City name"),
    state: str = typer.Option("UT", "--state", "-s", help="State code"),
    min_price: int = typer.Option(0, "--min-price", help="Minimum price"),
    max_price: int = typer.Option(999_999_999, "--max-price", help="Maximum price"),
    property_type: str = typer.Option(None, "--type", "-t", help="Property type: multi-family, single-family, any"),
    output: str = typer.Option(None, "--output", "-o", help="Export results to CSV file"),
    source: str = typer.Option(None, "--source", help="Scraper source: utahrealestate-api, utahrealestate-web, redfin, zillow"),
    search_area: int = typer.Option(50, "--search-area", help="Search area in square miles (for Zillow bounding box)"),
    per_bed_rent: float = typer.Option(600.0, "--per-bed-rent", help="Assumed rent per bedroom per month ($)"),
    down_pmt: float = typer.Option(0.25, "--down-pmt", help="Down payment percentage (0.25 = 25%)"),
    interest_rate: float = typer.Option(0.067, "--interest-rate", help="Annual mortgage interest rate"),
    insurance_rate: float = typer.Option(0.0043, "--insurance-rate", help="Annual insurance rate"),
    log: str = typer.Option(None, "--log", help="Write debug log to file (e.g. --log debug.log)"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug logging"),
):
    """Search for investment property listings."""
    if ctx.invoked_subcommand is not None:
        return

    _setup_logging(debug, log)

    console.print("\n[bold]What to Buy?[/bold]\n", style="cyan")

    interactive = not city
    if not city:
        city = typer.prompt("City")
    if interactive:
        state = typer.prompt("State", default=state)

    if interactive and min_price == 0:
        min_price_str = typer.prompt("Min price ($)", default="0")
        min_price = int(min_price_str.replace(",", "").replace("$", ""))
    if interactive and max_price == 999_999_999:
        max_price_str = typer.prompt("Max price ($)", default="999999")
        max_price = int(max_price_str.replace(",", "").replace("$", ""))

    if not property_type and interactive:
        prop_type = _property_type_prompt()
    elif not property_type:
        prop_type = PropertyType.MULTI_FAMILY
    else:
        type_map = {
            "multi-family": PropertyType.MULTI_FAMILY,
            "single-family": PropertyType.SINGLE_FAMILY,
            "any": PropertyType.ANY,
        }
        prop_type = type_map.get(property_type, PropertyType.MULTI_FAMILY)

    sources = [source] if source else []

    inv_params = InvestmentParams(
        per_bed_rent=per_bed_rent,
        down_pmt_pct=down_pmt,
        interest_rate=interest_rate,
        insurance_rate=insurance_rate,
    )

    criteria = SearchCriteria(
        city=city,
        state=state.upper(),
        min_price=min_price,
        max_price=max_price,
        property_type=prop_type,
        sources=sources,
        search_area_sqmi=search_area,
    )

    console.print(
        f"\nSearching for [bold]{prop_type}[/bold] properties in "
        f"[bold]{city}, {state.upper()}[/bold] "
        f"({_format_price(min_price)} - {_format_price(max_price)})...\n"
    )

    results = asyncio.run(find_listings(criteria, debug=debug))

    if not results:
        console.print("[yellow]No listings found.[/yellow] Try broadening your search.")
        raise typer.Exit()

    results.sort(key=lambda l: l.price)

    console.print()
    console.print(_build_table(results, inv_params))
    console.print(f"\n[green]Found {len(results)} listings.[/green]\n")

    if output:
        csv_path = Path(output)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(listings_to_csv(results, inv_params))
        console.print(f"Exported to [cyan]{csv_path}[/cyan]")
    else:
        export = typer.confirm("Export to CSV?", default=False)
        if export:
            filename = f"listings_{city.lower().replace(' ', '_')}_{state.lower()}.csv"
            with open(filename, "w", newline="", encoding="utf-8") as f:
                f.write(listings_to_csv(results, inv_params))
            console.print(f"Exported to [cyan]{filename}[/cyan]")


@app.command()
def market(
    city: str = typer.Option(..., "--city", "-c", help="City name"),
    state: str = typer.Option("UT", "--state", "-s", help="State code"),
    property_type: str = typer.Option("any", "--type", "-t", help="Property type: multi-family, single-family, any"),
    search_area: int = typer.Option(50, "--search-area", help="Search area in sq miles"),
    output: str = typer.Option(None, "--output", "-o", help="Export to CSV"),
    source: str = typer.Option(None, "--source", help="Scraper source"),
    log: str = typer.Option(None, "--log", help="Write debug log to file"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug logging"),
):
    """Market analytics: days on market, $/sqft, sale-to-list ratio, inventory."""
    _setup_logging(debug, log)

    console.print("\n[bold]Market Analytics[/bold]\n", style="cyan")

    type_map = {
        "multi-family": PropertyType.MULTI_FAMILY,
        "single-family": PropertyType.SINGLE_FAMILY,
        "any": PropertyType.ANY,
    }
    prop_type = type_map.get(property_type, PropertyType.ANY)

    sources = [source] if source else []

    criteria = SearchCriteria(
        city=city,
        state=state.upper(),
        property_type=prop_type,
        sources=sources,
        search_area_sqmi=search_area,
    )

    console.print(
        f"Analyzing [bold]{prop_type}[/bold] market in "
        f"[bold]{city}, {state.upper()}[/bold]...\n"
    )

    results = asyncio.run(find_listings(criteria, debug=debug))

    if not results:
        console.print("[yellow]No listings found.[/yellow] Try broadening your search.")
        raise typer.Exit()

    console.print()
    console.print(_build_overview_panel(results, city, state.upper()))
    console.print()
    console.print(_build_market_table(results))
    console.print(f"\n[green]Based on {len(results)} listings.[/green]\n")

    if output:
        csv_path = Path(output)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(listings_to_csv(results))
        console.print(f"Exported to [cyan]{csv_path}[/cyan]")


if __name__ == "__main__":
    app()
