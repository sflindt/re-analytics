"""LLM-powered area research for neighborhood news and context."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


async def fetch_area_news(city: str, state: str, max_tokens: int = 500) -> str | None:
    """Use Claude to generate a brief area news summary for a city.

    Returns a plain-text summary of recent (T12-24mo) real estate and
    community developments, or None if the API is unavailable.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

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
                    "model": "claude-haiku-4-5-20251001",
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
        logger.debug(f"Area news fetch failed: {e}")

    return None
