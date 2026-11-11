"""City lookup via Nominatim.

Usage policy: https://operations.osmfoundation.org/policies/nominatim/
One request per second, a identifiable User-Agent, and cached results.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from app.db import cache_get_geocode, cache_set_geocode
from app.errors import AppError
from app.services.http import get_client

logger = logging.getLogger("jewelleads.nominatim")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim asks for a maximum of 1 request per second.
_rate_lock = asyncio.Lock()
_last_request = 0.0


def normalise_query(city: str) -> str:
    return re.sub(r"\s+", " ", city.strip().casefold())


def area_id_for(osm_type: str, osm_id: int) -> int | None:
    """Overpass area id. Relations and closed ways can be areas; nodes cannot."""
    if osm_type == "relation":
        return 3_600_000_000 + osm_id
    if osm_type == "way":
        return 2_400_000_000 + osm_id
    return None


def _score(item: dict, query: str) -> float:
    try:
        score = float(item.get("importance") or 0)
    except (TypeError, ValueError):
        score = 0.0
    category = str(item.get("category") or item.get("class") or "").casefold()
    kind = str(item.get("type") or item.get("addresstype") or "").casefold()
    osm_type = str(item.get("osm_type") or "").casefold()
    if category == "boundary" or kind in {"administrative", "city", "town", "municipality"}:
        score += 1.5
    if kind in {"city", "town", "municipality"}:
        score += 1.0
    if osm_type == "relation":
        score += 0.75
    elif osm_type == "way":
        score += 0.25
    label = str(item.get("display_name") or "").casefold()
    if label.startswith(query):
        score += 0.5
    return score


def choose_place(results: list[dict], query: str) -> dict:
    """Pick the city/administrative result that best matches the typed name."""
    usable: list[dict] = []
    for item in results:
        bbox = item.get("boundingbox") or []
        if item.get("lat") is None or item.get("lon") is None or len(bbox) != 4:
            continue
        if not item.get("osm_id") or not item.get("osm_type"):
            continue
        usable.append(item)
    if not usable:
        raise AppError(
            f'No city named "{query}" was found. Check the spelling and try again.',
            status_code=404,
            code="city_not_found",
        )
    needle = normalise_query(query)
    best = max(usable, key=lambda item: _score(item, needle))
    try:
        south, north, west, east = (float(value) for value in best["boundingbox"])
        lat = float(best["lat"])
        lon = float(best["lon"])
        osm_id = int(best["osm_id"])
    except (TypeError, ValueError) as exc:
        raise AppError(
            "The city lookup returned an unexpected result. Try again.",
            status_code=502,
        ) from exc
    osm_type = str(best["osm_type"]).casefold()
    return {
        "query": query.strip(),
        "label": str(best.get("display_name") or query.strip()),
        "lat": lat,
        "lon": lon,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "bbox": [south, north, west, east],
        "area_id": area_id_for(osm_type, osm_id),
    }


async def _respect_rate_limit() -> None:
    global _last_request
    async with _rate_lock:
        wait = 1.0 - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()


async def geocode_city(city: str, *, refresh: bool = False) -> dict:
    """Resolve a city name to a label, map pin, bounding box, and Overpass area."""
    key = normalise_query(city)
    if not key:
        raise AppError("Enter a city name.", status_code=400)
    if not refresh:
        cached = cache_get_geocode(key)
        if cached:
            return cached

    await _respect_rate_limit()
    try:
        response = await get_client().get(
            NOMINATIM_URL,
            params={
                "q": city.strip(),
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 5,
            },
            headers={"Accept": "application/json"},
            timeout=20.0,
        )
    except httpx.TimeoutException as exc:
        raise AppError(
            "The city lookup timed out. Check your connection and try again.",
            status_code=504,
            code="timeout",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Nominatim request failed: %s", exc)
        raise AppError(
            "The city lookup failed. Check your connection and try again.",
            status_code=502,
        ) from exc

    if response.status_code in {429, 403}:
        raise AppError(
            "The city lookup service asked us to slow down. Wait a few seconds and try again.",
            status_code=429,
            code="rate_limited",
        )
    if response.status_code >= 400:
        logger.warning("Nominatim HTTP %s", response.status_code)
        raise AppError(
            "The city lookup failed. Try again in a moment.",
            status_code=502,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            "The city lookup returned an unreadable response. Try again.",
            status_code=502,
        ) from exc
    if not isinstance(payload, list):
        raise AppError("The city lookup returned an unexpected response.", status_code=502)

    place = choose_place(payload, city)
    cache_set_geocode(key, place)
    return place
