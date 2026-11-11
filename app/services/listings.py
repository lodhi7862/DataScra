"""Read public Google listing details for a shop OpenStreetMap already found.

OpenStreetMap often has the shop pin and no phone. The same shop's public
Google listing usually has the phone, a street address, opening hours, and a
rating. This asks for that one listing by name, near the known coordinates.

There is no API key. Google can refuse or change this response at any time.
Lookups are cached for 7 days and sent one at a time. Nothing here logs into
Google or solves a block page.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from urllib.parse import quote_plus, urlencode

import httpx

from app.db import cache_get_listing, cache_set_listing, country_code
from app.errors import AppError
from app.services.dedupe import coords_of, haversine_m, is_real_name, norm_name
from app.services.export import map_query
from app.services.http import get_client
from app.services.phone import choose_primary, normalize_many

logger = logging.getLogger("jewelleads.listings")

# Google's map-search response is the page a browser receives. The app's
# normal User-Agent gets an empty shell, so this one request uses a browser
# header. It is a single fixed string, not a rotating disguise.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_LISTING_URL = "https://www.google.com/search"
_PLACE_ID = re.compile(r"^0x[0-9a-f]+:0x[0-9a-f]+$")
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_SHORT_DAY = {
    "Monday": "Mon",
    "Tuesday": "Tue",
    "Wednesday": "Wed",
    "Thursday": "Thu",
    "Friday": "Fri",
    "Saturday": "Sat",
    "Sunday": "Sun",
}
_NEAR_M = 800
# A clearly named shop can sit a few kilometres from a rough OpenStreetMap pin.
_CITY_M = 8000
# How far a looser title may sit from the pin. One distinctive word, such as
# "saj" or "khan", only counts close to the door. Two or more can cover a
# pin that OpenStreetMap dropped a street or two away. A generic word such
# as "gold" never counts, in any city.
# Google sometimes has one listing for a shop name, a little farther than the pin.
_ONLY_M = 20000
_GENERIC_TOKENS = {
    "jewel",
    "gold",
    "shop",
    "house",
    "store",
    "new",
    "the",
    "and",
    "designer",
}
_MIN_GAP_SECONDS = 1.1

_gap_lock = asyncio.Lock()
_last_request = 0.0


def listing_name_match(wanted: str | None, found: str | None) -> bool:
    """True when two shop names are the same aside from jewel/jeweller spelling."""
    return _match_kind(wanted, found) is not None


def _match_kind(wanted: str | None, found: str | None) -> str | None:
    """'strong' when the names agree, 'weak' for a looser token overlap."""
    left = _fold_name(wanted)
    right = _fold_name(found)
    if not left or not right:
        return None
    if left == right or _name_is_prefix(left, right) or _name_is_prefix(right, left):
        return "strong"
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if len(short) >= 8 and short in long:
        return "strong"
    tokens_a, tokens_b = set(left.split()), set(right.split())
    if len(tokens_a) >= 2 and len(tokens_b) >= 2:
        overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
        if overlap >= 0.75:
            return "weak"
    # A short distinctive word such as "saj" is enough when the rest of the
    # title is generic (designer, jewellery). The caller keeps this within
    # a few hundred metres of the pin.
    if _shares_distinctive(left, right):
        return "close"
    return None


def _name_is_prefix(short: str, long: str) -> bool:
    """True when the map name is the start of a longer listing title.

    'Zee.sy' should match 'Zee.Sy Islamabad (Giga Boutique Mall)'. A single
    generic word such as 'gold' should not match every gold shop in the city.
    """
    if len(short) < 5 or not long.startswith(short):
        return False
    if len(long) != len(short) and long[len(short)] != " ":
        return False
    tokens = short.split()
    if len(tokens) >= 2:
        return True
    return len(tokens[0]) >= 6 and tokens[0] not in {"jewel", "gold", "shop"}


def _stem(token: str) -> str:
    if token in {"testing", "test"}:
        return "test"
    if token in {"laboratory", "laboratories", "lab", "labs"}:
        return "lab"
    return token


def _distinctive_tokens(folded: str) -> list[str]:
    return [
        _stem(token)
        for token in folded.split()
        if len(token) >= 3 and _stem(token) not in _GENERIC_TOKENS
    ]


def _shares_distinctive(wanted_folded: str, found_folded: str) -> bool:
    """Every distinctive word of the known name appears in the listing title."""
    distinctive = _distinctive_tokens(wanted_folded)
    if not distinctive:
        return False
    found_tokens = {_stem(token) for token in found_folded.split() if token}
    return all(token in found_tokens for token in distinctive)


def _distance_limit(kind: str, wanted: str) -> float:
    """Metres this kind of name agreement is allowed to travel from the pin."""
    if kind == "strong":
        return float(_CITY_M)
    if kind == "weak":
        return float(_NEAR_M)
    if kind != "close":
        return 0
    distinctive = _distinctive_tokens(_fold_name(wanted))
    if len(distinctive) >= 2:
        return 1500
    if distinctive and len(distinctive[0]) >= 4:
        return 500
    return 150


def _listing_url(name: object, lat: object, lng: object, place_id: object) -> str:
    """Open the business card itself, not a search results page."""
    if not place_id or lat in (None, "") or lng in (None, ""):
        return ""
    slug = quote_plus(str(name or "shop"))
    lat_text = f"{float(lat):.7f}".rstrip("0").rstrip(".")
    lng_text = f"{float(lng):.7f}".rstrip("0").rstrip(".")
    return f"https://www.google.com/maps/place/{slug}/@{lat_text},{lng_text},17z/data=!4m2!3m1!1s{place_id}"


def _only_named_result(payload: object, shop: dict) -> dict | None:
    """Use Google's only listing when the shop name is specific and the pin is in town.

    A one-word name, or a listing that shares no real word with the shop, stays unused.
    """
    wanted = _fold_name(shop.get("name"))
    if len([token for token in wanted.split() if token]) < 3:
        return None
    origin = coords_of(shop)
    if origin is None:
        return None
    seen: set[tuple] = set()
    places: list[dict] = []
    for node in _walk(payload):
        place = _unnamed_from_node(node)
        if place is None or place.get("lat") is None or place.get("lng") is None:
            continue
        key = (place["name"], round(float(place["lat"]), 5), round(float(place["lng"]), 5))
        if key in seen:
            continue
        seen.add(key)
        distance = haversine_m(origin, (float(place["lat"]), float(place["lng"])))
        if distance <= _ONLY_M:
            places.append(place)
    if len(places) != 1:
        return None
    place = places[0]
    shared = {_stem(token) for token in wanted.split() if token}
    shared &= {_stem(token) for token in _fold_name(place.get("name")).split() if token}
    shared -= _GENERIC_TOKENS
    if not shared:
        return None
    place.pop("_size", None)
    return place


def parse_listing_payload(payload: object, shop: dict) -> dict | None:
    """Pick the listing that matches this shop's name and is near its pin.

    The same rules apply in every city. A longer Google title is accepted
    when its distinctive words are the shop's words and the pin is close
    enough for that kind of match. The closest such listing wins.
    """
    wanted = str(shop.get("name") or "")
    origin = coords_of(shop)
    best: dict | None = None
    best_key: tuple | None = None
    seen: set[tuple] = set()
    for node in _walk(payload):
        place = _unnamed_from_node(node)
        if place is None or not place.get("name") or place.get("lat") is None or place.get("lng") is None:
            continue
        identity = (place["name"], round(float(place["lat"]), 5), round(float(place["lng"]), 5))
        if identity in seen:
            continue
        seen.add(identity)
        kind = _match_kind(wanted, str(place["name"]))
        if kind is None:
            continue
        if origin is None:
            if kind != "strong":
                continue
            distance = 0.0
        else:
            distance = haversine_m(origin, (float(place["lat"]), float(place["lng"])))
            if distance > _distance_limit(kind, wanted):
                continue
        # Prefer a record that has a phone, then the closer pin, then the
        # smaller record so a neighbouring shop's number is not borrowed.
        key = (0 if place.get("phone") else 1, round(distance / 50), int(place.get("_size") or 0))
        if best_key is None or key < best_key:
            best_key = key
            best = place
    if best is not None:
        best.pop("_size", None)
        return best
    return _only_named_result(payload, shop)


def merge_listing(shop: dict, details: dict | None, country: str) -> dict:
    """Fields worth copying onto the shop. Existing phone numbers stay put."""
    if not details:
        return {}
    updates: dict = {}
    found_name = str(details.get("name") or "").strip()
    current_name = str(shop.get("name") or "").strip()
    if found_name and (
        not is_real_name(current_name)
        or (_shares_distinctive(_fold_name(current_name), _fold_name(found_name)) and len(found_name) > len(current_name))
    ):
        updates["name"] = found_name
    if details.get("listing_url"):
        updates["listing_url"] = details["listing_url"]
    if not str(shop.get("phone") or "").strip() and details.get("phone"):
        parsed = normalize_many(str(details["phone"]), country)
        primary = choose_primary(parsed)
        if primary and primary.is_valid:
            updates["phone"] = primary.original or str(details["phone"])
            if primary.is_mobile and primary.normalized and not str(shop.get("whatsapp") or "").strip():
                updates["whatsapp"] = primary.normalized
        elif str(details["phone"]).strip():
            updates["phone"] = str(details["phone"]).strip()
    if not str(shop.get("website") or "").strip() and details.get("website"):
        updates["website"] = details["website"]
    if not str(shop.get("opening_hours") or "").strip() and details.get("opening_hours"):
        updates["opening_hours"] = details["opening_hours"]
    if shop.get("rating") in (None, "") and details.get("rating") is not None:
        updates["rating"] = details["rating"]
    current_address = str(shop.get("address") or "").strip()
    found_address = str(details.get("address") or "").strip()
    if found_address and (not current_address or len(found_address) > len(current_address) + 12):
        updates["address"] = found_address
    if not str(shop.get("area") or "").strip() and details.get("area"):
        updates["area"] = details["area"]
    if details.get("lat") is not None and details.get("lng") is not None:
        updates["lat"] = details["lat"]
        updates["lng"] = details["lng"]
    return updates


async def enrich_shops(shops: list[dict]) -> dict:
    """Look up each shop. A block from Google stops the rest of the batch."""
    country = country_code()
    results: list[dict] = []
    error = ""
    for shop in shops:
        if error:
            results.append({"found": False, "updates": {}})
            continue
        try:
            details = await lookup_listing(shop, country)
        except AppError as exc:
            error = exc.message
            results.append({"found": False, "updates": {}})
            continue
        results.append({"found": details is not None, "updates": merge_listing(shop, details, country)})
    return {"results": results, "error": error}


async def lookup_listing(shop: dict, country: str | None = None) -> dict | None:
    origin = coords_of(shop)
    unnamed = not is_real_name(shop.get("name"))
    query = "jewellery" if unnamed else map_query(shop)
    if not query or (unnamed and origin is None):
        return None
    key = _cache_key(shop, query)
    cached = cache_get_listing(key)
    if cached is not None:
        details = cached.get("details")
        return details if isinstance(details, dict) else None
    payload = await _fetch_listing(query, origin, country or country_code())
    if unnamed and origin is not None:
        details = nearest_place(payload, origin)
    else:
        details = parse_listing_payload(payload, shop)
    cache_set_listing(key, {"details": details})
    return details


def nearest_place(payload: object, origin: tuple[float, float], *, max_m: float = 200) -> dict | None:
    """The single business closest to an unnamed map pin, not a city-wide list."""
    best: dict | None = None
    best_key: tuple | None = None
    for node in _walk(payload):
        place = _unnamed_from_node(node)
        if place is None or place.get("lat") is None or place.get("lng") is None:
            continue
        distance = haversine_m(origin, (float(place["lat"]), float(place["lng"])))
        if distance > max_m:
            continue
        key = (round(distance), 0 if place.get("phone") else 1, int(place.get("_size") or 0))
        place.pop("_size", None)
        if best_key is None or key < best_key:
            best_key = key
            best = place
    return best


async def _fetch_listing(query: str, origin: tuple[float, float] | None, country: str) -> object:
    await _throttle()
    params = {"tbm": "map", "hl": "en", "q": query}
    if country == "92":
        params["gl"] = "pk"
    if origin is not None:
        lat, lng = origin
        # Center the search on the known pin so a same-named shop elsewhere loses.
        params["pb"] = f"!1s{query}!4m8!1m3!1d2000!2d{lng}!3d{lat}!3m2!1i1024!2i768!4f13.1!7i20!10b1"
    url = _LISTING_URL + "?" + urlencode(params)
    try:
        response = await get_client().get(
            url,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept-Language": "en",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=httpx.Timeout(25.0, connect=10.0),
        )
    except httpx.TimeoutException as exc:
        raise AppError(
            "Google took too long to return listing details. Try again in a minute.",
            status_code=504,
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            "Could not reach Google for listing details. The shop list is unchanged.",
            status_code=502,
        ) from exc
    text = response.text or ""
    lowered = text.casefold()
    blocked = response.status_code in {429, 403} or "unusual traffic" in lowered or "consent.google" in str(response.url)
    if blocked:
        logger.warning("Google refused a listing lookup (HTTP %s)", response.status_code)
        raise AppError(
            "Google is limiting listing lookups from this computer. Wait a few minutes and search again. Numbers already filled stay saved.",
            status_code=503,
        )
    if response.status_code != 200:
        raise AppError(
            "Google did not return listing details. The shop list is unchanged.",
            status_code=502,
        )
    start = text.find("[")
    if start < 0:
        return []
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        logger.warning("Google listing response was not readable JSON")
        return []


def _unnamed_from_node(node: list) -> dict | None:
    strings = [item.strip() for item in node if isinstance(item, str) and item.strip()]
    name = _name_after_place_id(strings)
    if not name:
        return None
    point = None
    for child in _walk(node):
        found = _coords(child)
        if found is not None:
            point = found
            break
    if point is None:
        return None
    address = _address(strings, name)
    place_id = next((item for item in strings if _PLACE_ID.match(item)), "")
    return {
        "name": name,
        "phone": _phone_value(node),
        "website": _website(node),
        "opening_hours": _hours(node),
        "rating": _rating(node),
        "address": address,
        "area": _area(strings, name, address),
        "lat": point[0],
        "lng": point[1],
        "place_id": place_id,
        "listing_url": _listing_url(name, point[0], point[1], place_id),
        "_size": sum(1 for _ in _walk(node)),
    }


def _name_after_place_id(strings: list[str]) -> str:
    for index, item in enumerate(strings):
        if not _PLACE_ID.match(item):
            continue
        for nxt in strings[index + 1 : index + 5]:
            if _human_name(nxt):
                return nxt
    return ""


def _human_name(value: str) -> str:
    text = value.strip()
    if not text or not 3 <= len(text) <= 80:
        return ""
    if "," in text or text.casefold().endswith("pakistan"):
        return ""
    if text.startswith(("http", "0x", "/", "2ahUK")):
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{12,}", text):
        return ""
    if text.casefold() in {"en", "ur", "jewelry store", "jeweler", "jewellery", "jewelry"}:
        return ""
    letters = re.sub(r"[^A-Za-z\u0600-\u06FF]", "", text)
    return text if len(letters) >= 3 else ""


def _address(strings: list[str], name: str) -> str:
    candidates = [
        item
        for item in strings
        if "," in item and not item.startswith("http") and 12 < len(item) < 300 and not _PLACE_ID.match(item)
    ]
    plain = [item for item in candidates if name.casefold() not in item.casefold()]
    pool = plain or candidates
    if not pool:
        return ""
    return max(pool, key=len)


def _area(strings: list[str], name: str, address: str) -> str:
    skipped = {name.casefold(), address.casefold()}
    short = [
        item
        for item in strings
        if item.casefold() not in skipped
        and "," not in item
        and "/" not in item
        and not item.startswith("http")
        and not _PLACE_ID.match(item)
        and not re.fullmatch(r"[A-Za-z0-9_-]{12,}", item)
        and 3 <= len(item) <= 40
        and not item.startswith("0x")
        and " " not in item.strip()
    ]
    unique = list(dict.fromkeys(short))
    return unique[0] if len(unique) == 1 else ""


def _phone_value(node: list) -> str:
    labeled = _labeled(node, "call_googblue")
    if labeled:
        return labeled
    # Some listings omit the call icon and store the number as
    # ["+92 321 3800037", [["0321 3800037", 1], ["+92 321 3800037", 2]], ...].
    for item in _walk(node):
        if len(item) < 2 or not isinstance(item[0], str) or not isinstance(item[1], list):
            continue
        text = item[0].strip()
        if not _looks_like_phone(text):
            continue
        pairs = [pair for pair in item[1] if isinstance(pair, list) and len(pair) >= 2]
        if pairs and all(isinstance(pair[0], str) and isinstance(pair[1], int) for pair in pairs[:2]):
            return text
    return ""


def _looks_like_phone(value: str) -> bool:
    if not value or value.casefold().startswith("add "):
        return False
    digits = re.sub(r"\D", "", value)
    return 8 <= len(digits) <= 15 and bool(re.fullmatch(r"[+\d][\d\s\-()]+", value))


def _labeled(node: list, icon: str) -> str:
    for item in _walk(node):
        if len(item) < 3 or not isinstance(item[2], list) or len(item[2]) < 2:
            continue
        image, label = item[2][0], item[2][1]
        if not isinstance(image, str) or icon not in image or not isinstance(label, str):
            continue
        text = label.strip()
        if text and "add " not in text.casefold() and any(character.isdigit() for character in text):
            return text
    return ""


def _website(node: list) -> str:
    for item in _walk(node):
        if len(item) < 3 or not isinstance(item[2], list) or len(item[2]) < 2:
            continue
        image, label = item[2][0], item[2][1]
        if not isinstance(image, str) or "public_googblue" not in image or not isinstance(label, str):
            continue
        text = label.strip()
        if text.startswith("http") and "google." not in text.casefold() and "gstatic." not in text.casefold():
            return text
    return ""


def _hours(node: list) -> str:
    found: dict[str, str] = {}
    for item in _walk(node):
        if len(item) < 4 or item[0] not in _DAYS or not isinstance(item[3], list):
            continue
        slot = ""
        block = item[3]
        if block and isinstance(block[0], list) and block[0] and isinstance(block[0][0], str):
            slot = block[0][0]
        elif block and isinstance(block[0], str):
            slot = block[0]
        found.setdefault(str(item[0]), _tidy(slot) or "Closed")
    if not found:
        return ""
    slots = [found[day] for day in _DAYS if day in found]
    if len(found) == 7 and len(set(slots)) == 1 and slots[0] != "Closed":
        return f"Daily {slots[0]}"
    groups: list[str] = []
    days = [day for day in _DAYS if day in found]
    start = previous = days[0]
    slot = found[start]

    def flush(first: str, last: str, hours: str) -> None:
        label = _SHORT_DAY[first] if first == last else f"{_SHORT_DAY[first]}-{_SHORT_DAY[last]}"
        groups.append(f"{label} closed" if hours == "Closed" else f"{label} {hours}")

    for day in days[1:]:
        follows = _DAYS.index(day) == _DAYS.index(previous) + 1
        if follows and found[day] == slot:
            previous = day
            continue
        flush(start, previous, slot)
        start = previous = day
        slot = found[day]
    flush(start, previous, slot)
    return "; ".join(groups)


def _rating(node: list) -> float | None:
    fallback: float | None = None
    for item in _walk(node):
        if len(item) > 12:
            continue
        texts = " ".join(str(value) for value in item if isinstance(value, str)).casefold()
        for index, value in enumerate(item[:-1]):
            nxt = item[index + 1]
            if not isinstance(value, float) or not 1 <= value <= 5:
                continue
            if not isinstance(nxt, int) or not 1 <= nxt <= 50000:
                continue
            rounded = round(float(value), 1)
            if "review" in texts:
                return rounded
            if fallback is None:
                fallback = rounded
    return fallback


def _coords(node: list) -> tuple[float, float] | None:
    if len(node) < 4 or node[0] is not None or node[1] is not None:
        return None
    if not isinstance(node[2], (int, float)) or not isinstance(node[3], (int, float)):
        return None
    lat, lng = float(node[2]), float(node[3])
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return None
    # A bare zoom or pixel pair is not a shop. Listings use a 4-item point.
    if len(node) != 4:
        return None
    return lat, lng


def _walk(node: object):
    if isinstance(node, list):
        yield node
        for child in node:
            yield from _walk(child)
    elif isinstance(node, dict):
        for child in node.values():
            yield from _walk(child)


def _fold_name(value: str | None) -> str:
    text = norm_name(value)
    tokens = [_fold_jewel(token) for token in text.split()]
    return " ".join(tokens)


def _fold_jewel(token: str) -> str:
    """Treat jewellery misspellings as the same word.

    OpenStreetMap often has 'jewlery', 'jewllers', or 'jwellery'.
    """
    if token in {"jewish", "jewel"}:
        return token
    if "l" in token and re.fullmatch(r"j[ew]+l[a-z]{0,8}", token):
        return "jewel"
    return token


def _tidy(value: str) -> str:
    text = value.replace("\u202f", " ").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip()


def _cache_key(shop: dict, query: str) -> str:
    point = coords_of(shop)
    where = "" if point is None else f"{point[0]:.4f},{point[1]:.4f}"
    raw = f"v7|{norm_name(shop.get('name'))}|{where}|{query.casefold()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _throttle() -> None:
    global _last_request
    async with _gap_lock:
        wait = _MIN_GAP_SECONDS - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()
