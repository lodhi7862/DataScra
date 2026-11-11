"""Find jewellery shops with the public Overpass API.

No API key. Results are cached for 24 hours. If the main server fails, a
second public mirror is tried. Cities without a polygon use a bounding box.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from app.db import cache_get_search, cache_set_search, country_code
from app.errors import AppError
from app.models import CHIP_PATTERNS, DEFAULT_NAME_REGEX
from app.services.dedupe import collapse_duplicates, refresh_numbers
from app.services.http import get_client
from app.services.nominatim import geocode_city
from app.services.phone import choose_primary, normalize_many, normalize_phone

logger = logging.getLogger("jewelleads.osm")

OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

JEWEL_SHOPS = {"jewelry", "jewellery"}
JEWEL_CRAFTS = {"goldsmith", "jeweller"}
ALLOW_SHOPS = JEWEL_SHOPS | {
    "watches",
    "clock",
    "clocks",
    "accessories",
    "gift",
    "antiques",
    "art",
    "craft",
    "boutique",
    "yes",
    "trade",
    "wholesale",
    "pawnbroker",
}
RESULT_CAP = 400

_SAFE_REGEX = re.compile(r"[A-Za-z0-9|.*+?()_\-\s]+")
_SAFE_LITERAL = re.compile(r"[\w .'\-]{2,40}", re.UNICODE)


class _OverpassFailed(Exception):
    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


def build_name_regex(chips: list[str], keyword: str) -> str:
    """Turn chips and an optional keyword into one case-insensitive name pattern."""
    parts: list[str] = []
    for chip in chips:
        pattern = CHIP_PATTERNS.get(chip)
        if pattern:
            parts.append(pattern)
    custom = (keyword or "").strip()
    if custom:
        if "|" in custom:
            if not _SAFE_REGEX.fullmatch(custom):
                raise AppError(
                    "Custom keyword patterns may only use letters, numbers, spaces, and | . * + ? ( ) _ -.",
                    status_code=400,
                )
            parts.append(custom)
        else:
            tokens = [token.strip() for token in custom.split(",") if token.strip()]
            escaped: list[str] = []
            for token in tokens:
                if not _SAFE_LITERAL.fullmatch(token):
                    raise AppError(
                        "Keywords may only use letters, numbers, spaces, apostrophes, and hyphens.",
                        status_code=400,
                    )
                escaped.append(re.escape(token))
            if escaped:
                parts.append("|".join(escaped))
    pattern = "|".join(parts) if parts else DEFAULT_NAME_REGEX
    if len(pattern) > 180:
        raise AppError("That keyword list is too long.", status_code=400)
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise AppError("That keyword pattern is not valid.", status_code=400) from exc
    return pattern


def build_overpass_query(
    name_regex: str,
    area_id: int | None,
    bbox: list[float] | None,
) -> str:
    # The regex is already restricted to a safe character set. Drop quotes anyway.
    safe = name_regex.replace("\\", "\\\\").replace('"', "")
    if area_id is not None:
        scope = "(area.searchArea)"
        prelude = f"area({int(area_id)})->.searchArea;\n"
    else:
        if not bbox or len(bbox) != 4:
            raise AppError("This city has no map boundary to search.", status_code=404)
        south, north, west, east = bbox
        # Overpass expects (south, west, north, east).
        scope = f"({south},{west},{north},{east})"
        prelude = ""
    return (
        "[out:json][timeout:55];\n"
        f"{prelude}"
        "(\n"
        f'  nwr["shop"="jewelry"]{scope};\n'
        f'  nwr["shop"="jewellery"]{scope};\n'
        f'  nwr["craft"="goldsmith"]{scope};\n'
        f'  nwr["craft"="jeweller"]{scope};\n'
        f'  nwr["shop"]["name"~"{safe}",i]{scope};\n'
        f'  nwr["craft"]["name"~"{safe}",i]{scope};\n'
        ");\n"
        "out center tags;\n"
    )


def _keep(tags: dict, pattern: re.Pattern[str]) -> bool:
    shop = str(tags.get("shop") or "").casefold()
    craft = str(tags.get("craft") or "").casefold()
    name = str(tags.get("name") or tags.get("name:en") or tags.get("brand") or "")
    if shop in JEWEL_SHOPS or craft in JEWEL_CRAFTS:
        return True
    if not name or pattern.search(name) is None:
        return False
    if shop in ALLOW_SHOPS or "jewel" in shop or "gold" in shop:
        return True
    if craft and (craft in {"watchmaker", "gold", "silversmith"} or "jewel" in craft or "gold" in craft):
        return True
    return False


def _first(tags: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(tags.get(key) or "").strip()
        if value:
            return value
    return ""


def _website(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("www."):
        return "https://" + value
    return value


def _social(value: str, host: str) -> str:
    value = value.strip()
    if not value:
        return ""
    lowered = value.casefold()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return value
    if host in lowered:
        return "https://" + value.lstrip("/")
    return f"https://{host}/{value.lstrip('/')}"


def _format_address(tags: dict) -> str:
    line = " ".join(
        part
        for part in (str(tags.get("addr:housenumber") or "").strip(), str(tags.get("addr:street") or "").strip())
        if part
    )
    parts: list[str] = []
    if line:
        parts.append(line)
    for key in (
        "addr:place",
        "addr:suburb",
        "addr:neighbourhood",
        "addr:quarter",
        "addr:district",
        "addr:city",
        "addr:postcode",
    ):
        value = str(tags.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    if parts:
        return ", ".join(parts)
    return str(tags.get("addr:full") or "").strip()


def _point(element: dict) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        try:
            return round(float(element["lat"]), 6), round(float(element["lon"]), 6)
        except (TypeError, ValueError):
            return None
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        try:
            return round(float(center["lat"]), 6), round(float(center["lon"]), 6)
        except (TypeError, ValueError):
            return None
    return None


def parse_elements(
    elements: list[dict],
    name_regex: str,
    city_name: str,
    country: str,
) -> list[dict]:
    pattern = re.compile(name_regex, re.IGNORECASE)
    shops: list[dict] = []
    seen_ids: set[str] = set()
    for element in elements:
        osm_type = str(element.get("type") or "")
        osm_id = str(element.get("id") or "")
        identity = f"{osm_type}/{osm_id}"
        if not osm_id or identity in seen_ids:
            continue
        tags = element.get("tags") or {}
        if not isinstance(tags, dict) or not _keep(tags, pattern):
            continue
        point = _point(element)
        if point is None:
            continue
        seen_ids.add(identity)
        shops.append(_shop_from_tags(tags, osm_type, osm_id, point, city_name, country))
    collapsed = collapse_duplicates(shops, country, meters=150)
    refreshed = [refresh_numbers(shop, country) for shop in collapsed]
    refreshed.sort(key=lambda shop: str(shop.get("name") or "").casefold())
    return refreshed[:RESULT_CAP]


def _shop_from_tags(
    tags: dict,
    osm_type: str,
    osm_id: str,
    point: tuple[float, float],
    city_name: str,
    country: str,
) -> dict:
    phone_raw = " ; ".join(
        part
        for part in (
            _first(tags, ("phone", "contact:phone", "telephone")),
            _first(tags, ("mobile", "contact:mobile")),
        )
        if part
    )
    parsed = normalize_many(phone_raw, country)
    primary = choose_primary(parsed)
    wa_raw = _first(tags, ("contact:whatsapp", "whatsapp"))
    wa_parsed = normalize_phone(wa_raw, country) if wa_raw else None
    if wa_parsed and wa_parsed.is_mobile and wa_parsed.normalized:
        whatsapp = wa_parsed.normalized
    else:
        mobile = next((item for item in parsed if item.is_mobile), None)
        whatsapp = mobile.normalized if mobile else ""

    website = _website(_first(tags, ("website", "contact:website", "url")))
    facebook = _social(_first(tags, ("contact:facebook", "facebook")), "facebook.com")
    instagram = _social(_first(tags, ("contact:instagram", "instagram")), "instagram.com")
    if "facebook.com" in website.casefold() or "fb.com" in website.casefold():
        facebook = facebook or website
        website = ""
    if "instagram.com" in website.casefold():
        instagram = instagram or website
        website = ""

    email = _first(tags, ("email", "contact:email"))
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        email = ""

    area = _first(
        tags,
        ("addr:suburb", "addr:neighbourhood", "addr:quarter", "addr:district"),
    )
    name = _first(tags, ("name", "name:en", "brand", "operator")) or "Jewellery shop"
    return {
        "name": name,
        "phone": "; ".join(item.original.strip() for item in parsed) if parsed else phone_raw,
        "phone_normalized": primary.normalized if primary else "",
        "whatsapp": whatsapp or "",
        "email": email,
        "website": website,
        "facebook": facebook,
        "instagram": instagram,
        "address": _format_address(tags),
        "city": _first(tags, ("addr:city",)) or city_name,
        "area": area,
        "lat": point[0],
        "lng": point[1],
        "opening_hours": _first(tags, ("opening_hours",)),
        "osm_type": osm_type,
        "osm_id": osm_id,
        "source": "OpenStreetMap",
    }


def filter_by_area(shops: list[dict], area: str) -> list[dict]:
    needle = area.strip().casefold()
    if not needle:
        return shops
    kept: list[dict] = []
    for shop in shops:
        haystack = " ".join(
            str(shop.get(field) or "") for field in ("address", "area", "city")
        ).casefold()
        if needle in haystack:
            kept.append(shop)
    return kept


async def _post_overpass(url: str, query: str) -> dict:
    try:
        response = await get_client().post(
            url,
            data={"data": query},
            headers={"Accept": "application/json"},
            timeout=60.0,
        )
    except httpx.TimeoutException as exc:
        raise _OverpassFailed("timed out", "timeout") from exc
    except httpx.HTTPError as exc:
        raise _OverpassFailed("network error", "network") from exc
    if response.status_code == 429:
        raise _OverpassFailed("busy", "busy")
    if response.status_code >= 400:
        raise _OverpassFailed(f"HTTP {response.status_code}", "http")
    try:
        payload = response.json()
    except ValueError as exc:
        raise _OverpassFailed("unreadable response", "bad") from exc
    if not isinstance(payload, dict) or "elements" not in payload:
        remark = str(payload.get("remark") if isinstance(payload, dict) else "") or "no data"
        kind = "area" if "area" in remark.casefold() or "runtime error" in remark.casefold() else "bad"
        raise _OverpassFailed(remark[:180], kind)
    return payload


async def run_overpass(query: str) -> dict:
    """POST the query to the main Overpass server, then a mirror if that fails."""
    errors: list[_OverpassFailed] = []
    for url in OVERPASS_MIRRORS:
        try:
            payload = await _post_overpass(url, query)
            logger.info("Overpass query succeeded via %s (%s elements)", url, len(payload.get("elements") or []))
            return payload
        except _OverpassFailed as exc:
            logger.warning("Overpass %s failed (%s): %s", url, exc.kind, exc)
            errors.append(exc)
    kinds = {error.kind for error in errors}
    if kinds <= {"area"}:
        raise AppError("The city boundary could not be used.", status_code=502, code="area")
    if "timeout" in kinds or "busy" in kinds:
        raise AppError(
            "OpenStreetMap's search servers are busy and the request timed out. "
            "Wait a minute and try again — a repeat of the same search is cached.",
            status_code=504,
            code="timeout",
        )
    raise AppError(
        "OpenStreetMap search is unavailable right now. Check your connection and try again.",
        status_code=502,
        code="upstream",
    )


def _cache_key(place: dict, name_regex: str) -> str:
    raw = "|".join(
        [
            str(place.get("osm_type") or ""),
            str(place.get("osm_id") or ""),
            name_regex.casefold(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def search_shops(
    city: str,
    chips: list[str],
    keyword: str,
    area: str,
    *,
    refresh: bool = False,
) -> dict:
    """Geocode a city, query Overpass, and return cleaned shop records."""
    name_regex = build_name_regex(chips, keyword)
    place = await geocode_city(city, refresh=refresh)
    key = _cache_key(place, name_regex)
    cached_payload = None if refresh else cache_get_search(key)
    used_bbox = False
    from_cache = cached_payload is not None

    if cached_payload is None:
        area_id = place.get("area_id")
        bbox = place.get("bbox")
        if area_id:
            try:
                payload = await run_overpass(build_overpass_query(name_regex, int(area_id), None))
            except AppError as exc:
                if exc.code != "area" or not bbox:
                    raise
                logger.info("Area %s unavailable, falling back to bounding box", area_id)
                payload = await run_overpass(build_overpass_query(name_regex, None, bbox))
                used_bbox = True
        else:
            payload = await run_overpass(build_overpass_query(name_regex, None, bbox))
            used_bbox = True
        country = country_code()
        shops = parse_elements(payload.get("elements") or [], name_regex, city.strip(), country)
        cached_payload = {"shops": shops, "used_bbox": used_bbox}
        cache_set_search(key, cached_payload)
    else:
        used_bbox = bool(cached_payload.get("used_bbox"))

    shops = filter_by_area(list(cached_payload.get("shops") or []), area)
    truncated = len(cached_payload.get("shops") or []) >= RESULT_CAP
    return {
        "cached": from_cache,
        "used_bbox": used_bbox,
        "truncated": truncated,
        "place": {
            "label": place.get("label") or city.strip(),
            "lat": place.get("lat"),
            "lon": place.get("lon"),
            "osm_type": place.get("osm_type"),
            "osm_id": place.get("osm_id"),
        },
        "keyword": name_regex,
        "area": area.strip(),
        "shops": shops,
    }
