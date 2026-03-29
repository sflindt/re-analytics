"""Simple file-based cache for search results."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from re_analytics.models import Listing, SearchCriteria

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".re-analytics" / "cache"
DEFAULT_TTL = 3600  # 1 hour


def _cache_key(criteria: SearchCriteria) -> str:
    """Generate a deterministic cache key from search criteria."""
    key_parts = (
        criteria.city.lower().strip(),
        criteria.state.upper().strip(),
        str(criteria.property_type),
        str(criteria.min_price),
        str(criteria.max_price),
        str(criteria.search_area_sqmi),
    )
    raw = "|".join(key_parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_cached(criteria: SearchCriteria, ttl: int = DEFAULT_TTL) -> list[Listing] | None:
    """Return cached listings if fresh enough, else None."""
    cache_file = CACHE_DIR / f"{_cache_key(criteria)}.json"
    if not cache_file.exists():
        return None

    try:
        data = json.loads(cache_file.read_text())
        cached_at = data.get("cached_at", 0)
        if time.time() - cached_at > ttl:
            logger.debug(f"Cache expired for {criteria.city}, {criteria.state}")
            return None

        listings = [Listing(**item) for item in data.get("listings", [])]
        logger.debug(f"Cache hit: {len(listings)} listings for {criteria.city}, {criteria.state}")
        return listings
    except Exception as e:
        logger.debug(f"Cache read failed: {e}")
        return None


def list_cached() -> list[dict]:
    """List all cached searches with metadata (for the 'load previous' UI)."""
    if not CACHE_DIR.exists():
        return []
    results = []
    for f in sorted(CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            age_hrs = (time.time() - data.get("cached_at", 0)) / 3600
            results.append({
                "file": f.name,
                "city": data.get("city", "?"),
                "state": data.get("state", "?"),
                "property_type": data.get("property_type", ""),
                "count": data.get("count", 0),
                "age_hours": round(age_hrs, 1),
                "cached_at": data.get("cached_at", 0),
            })
        except Exception:
            continue
    return results


def load_cached_file(filename: str) -> list[Listing] | None:
    """Load listings from a specific cache file (ignoring TTL)."""
    cache_file = CACHE_DIR / filename
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
        return [Listing(**item) for item in data.get("listings", [])]
    except Exception as e:
        logger.debug(f"Failed to load cache file {filename}: {e}")
        return None


def set_cached(criteria: SearchCriteria, listings: list[Listing]) -> None:
    """Store listings in cache."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"{_cache_key(criteria)}.json"

        data = {
            "cached_at": time.time(),
            "city": criteria.city,
            "state": criteria.state,
            "property_type": criteria.property_type,
            "count": len(listings),
            "listings": [asdict(l) for l in listings],
        }
        cache_file.write_text(json.dumps(data, default=str))
        logger.debug(f"Cached {len(listings)} listings for {criteria.city}, {criteria.state}")
    except Exception as e:
        logger.debug(f"Cache write failed: {e}")
