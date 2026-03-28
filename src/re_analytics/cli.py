"""CLI entry point for re-analytics."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer
from rich.console import Console
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
    help="Real estate investment property finder.",
)
console = Console()


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
    table.add_column("Source", style="dim")

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
            l.source,
        )
    return table


@app.callback(invoke_without_command=True)
def listings(
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
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug logging of HTTP requests/responses"),
):
    """Search for investment property listings."""
    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    console.print("\n[bold]What to Buy?[/bold]\n", style="cyan")

    # Interactive prompts only when values are missing
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
        if debug:
            console.print(
                "\n[dim]Debug logging enabled — check the output above for HTTP status codes "
                "and response details from each scraper.[/dim]"
            )
        raise typer.Exit()

    # Sort by price
    results.sort(key=lambda l: l.price)

    console.print()
    console.print(_build_table(results, inv_params))
    console.print(f"\n[green]Found {len(results)} listings.[/green]\n")

    # Export to CSV
    if output:
        csv_path = Path(output)
        csv_path.write_text(listings_to_csv(results, inv_params))
        console.print(f"Exported to [cyan]{csv_path}[/cyan]")
    else:
        export = typer.confirm("Export to CSV?", default=False)
        if export:
            filename = f"listings_{city.lower().replace(' ', '_')}_{state.lower()}.csv"
            Path(filename).write_text(listings_to_csv(results, inv_params))
            console.print(f"Exported to [cyan]{filename}[/cyan]")


if __name__ == "__main__":
    app()
