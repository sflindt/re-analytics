"""LLM-powered area research and market commentary."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"


async def _call_claude(prompt: str, max_tokens: int = 500) -> str | None:
    """Call Claude Haiku and return text response, or None on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": HAIKU_MODEL,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if resp.status_code != 200:
                logger.debug(f"Claude API returned {resp.status_code}: {resp.text[:200]}")
                return None

            data = resp.json()
            content = data.get("content", [])
            if content and content[0].get("type") == "text":
                return content[0]["text"]

    except Exception as e:
        logger.debug(f"Claude API call failed: {e}")

    return None


async def fetch_area_news(city: str, state: str, max_tokens: int = 500) -> str | None:
    """Use Claude to generate a brief area news summary for a city.

    Returns a plain-text summary of recent (T12-24mo) real estate and
    community developments, or None if the API is unavailable.
    """
    prompt = (
        f"Provide a brief summary (3-5 bullet points) of notable real estate and community "
        f"developments in {city}, {state} from the past 12-24 months. Focus on:\n"
        f"- Major residential development projects or zoning changes\n"
        f"- Notable employer moves (new HQs, expansions, closures)\n"
        f"- Infrastructure projects (transit, roads, schools)\n"
        f"- Population or demographic trends\n"
        f"- Any significant market shifts\n\n"
        f"Be factual and concise. Use plain ASCII characters only (no em dashes or special "
        f"characters). If you are unsure about a specific detail, omit it rather than guess."
    )
    return await _call_claude(prompt, max_tokens)


async def fetch_market_commentary(
    city: str,
    state: str,
    is_investment: bool,
    stats: dict,
    max_tokens: int = 600,
) -> str | None:
    """Generate LLM-powered market commentary from listing statistics.

    Args:
        city: Target city name
        state: State abbreviation
        is_investment: Whether in investment mode
        stats: Dict with keys like 'active_count', 'median_price', 'median_dom',
               'median_ppsf', 'mortgage_rate', 'fed_funds_rate', 'spread',
               'yoy_appreciation', 'population', 'pop_yoy_change'
    """
    mode = "investment property buyer" if is_investment else "home buyer"

    data_lines = []
    if stats.get("active_count"):
        data_lines.append(f"- Active listings: {stats['active_count']}")
    if stats.get("median_price"):
        data_lines.append(f"- Median list price: ${stats['median_price']:,.0f}")
    if stats.get("median_dom") is not None:
        data_lines.append(f"- Median days on market: {stats['median_dom']:.0f}")
    if stats.get("median_ppsf"):
        data_lines.append(f"- Median $/sqft: ${stats['median_ppsf']:,.0f}")
    if stats.get("mortgage_rate"):
        data_lines.append(f"- 30yr mortgage rate: {stats['mortgage_rate']:.2f}%")
    if stats.get("fed_funds_rate"):
        data_lines.append(f"- Fed funds rate: {stats['fed_funds_rate']:.2f}%")
    if stats.get("spread"):
        data_lines.append(f"- Mortgage-Treasury spread: {stats['spread']:.2f}%")
    if stats.get("yoy_appreciation") is not None:
        data_lines.append(f"- YoY home price appreciation: {stats['yoy_appreciation']:.1f}%")
    if stats.get("population"):
        pop_line = f"- City population: {stats['population']:,}"
        if stats.get("pop_yoy_change") is not None:
            pop_line += f" ({stats['pop_yoy_change']:+.1f}% YoY)"
        data_lines.append(pop_line)

    data_block = "\n".join(data_lines) if data_lines else "Limited data available."

    prompt = (
        f"You are a real estate market analyst writing a brief commentary for a {mode} "
        f"evaluating properties in {city}, {state}.\n\n"
        f"Current market data:\n{data_block}\n\n"
        f"Write 2-3 concise paragraphs covering:\n"
        f"1. NATIONAL OUTLOOK: Rate environment and what it means for this {mode}\n"
        f"2. LOCAL MARKET: What the inventory, DOM, and pricing data reveal about "
        f"the {city} market\n"
        f"3. KEY TAKEAWAY: One actionable insight for the {mode}\n\n"
        f"Use headers like **National Outlook**, **{city} Market**, **Key Takeaway**.\n"
        f"Be data-driven, reference the specific numbers provided. No speculation. "
        f"Use plain ASCII characters only (no em dashes or special unicode). "
        f"Keep it under 200 words total."
    )
    return await _call_claude(prompt, max_tokens)
