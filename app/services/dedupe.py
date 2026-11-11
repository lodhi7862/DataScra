"""Decide when two leads are the same shop, and how to merge them.

Order, matching the product rules:
1. Same normalised phone number.
2. Otherwise a similar name and an approximate location (about 200 metres).
When a lead is already in the CRM, filled-in fields win over blanks, and the
status, follow-up date, and source the user already chose are left alone.
"""

from __future__ import annotations

import math
import re

from app.services.phone import normalize_many, normalize_phone

PLACEHOLDER_NAMES = {
    "",
    "unnamed",
    "unnamed shop",
    "jewellery shop",
    "jewelry shop",
    "jewellers",
    "unknown",
}

_CONTACT_STRINGS = (
    "email",
    "website",
    "facebook",
    "instagram",
    "address",
    "city",
    "area",
    "opening_hours",
    "notes",
    "listing_url",
)


def norm_name(value: str | None) -> str:
    text = (value or "").casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_real_name(value: str | None) -> bool:
    cleaned = norm_name(value)
    return cleaned not in PLACEHOLDER_NAMES and len(cleaned) >= 2


def names_similar(left: str | None, right: str | None) -> bool:
    a = norm_name(left)
    b = norm_name(right)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 10 and len(short.split()) >= 2 and short in long:
        return True
    tokens_a, tokens_b = set(a.split()), set(b.split())
    if len(tokens_a) < 2 or len(tokens_b) < 2:
        return False
    overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    return overlap >= 0.75


def coords_of(record: dict) -> tuple[float, float] | None:
    try:
        lat = float(record.get("lat"))  # type: ignore[arg-type]
        lng = float(record.get("lng"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    if lat == 0 and lng == 0:
        return None
    return lat, lng


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def numbers_of(record: dict, country_code: str = "92") -> set[str]:
    found: set[str] = set()
    for field in ("phone", "whatsapp", "phone_normalized"):
        raw = str(record.get(field) or "")
        if not raw:
            continue
        for part in re.split(r"[;|/]|,(?=\s*\+?\d)", raw):
            result = normalize_phone(part, country_code)
            if result.normalized:
                found.add(result.normalized)
    return found


def same_city(left: dict, right: dict) -> bool:
    a = str(left.get("city") or "").strip().casefold()
    b = str(right.get("city") or "").strip().casefold()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def is_same_lead(
    left: dict,
    right: dict,
    country_code: str = "92",
    meters: float = 200.0,
) -> bool:
    """True when two records should be one lead."""
    left_numbers = numbers_of(left, country_code)
    right_numbers = numbers_of(right, country_code)
    if left_numbers and right_numbers and (left_numbers & right_numbers):
        return True
    if not names_similar(left.get("name"), right.get("name")):
        return False
    left_xy = coords_of(left)
    right_xy = coords_of(right)
    if left_xy and right_xy:
        return haversine_m(left_xy, right_xy) <= meters
    # No pin: only an exact name in the same city is strong enough.
    if norm_name(left.get("name")) == norm_name(right.get("name")) and same_city(left, right):
        return True
    return False


def _blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def merge_into_existing(existing: dict, incoming: dict) -> dict:
    """Fill gaps on a saved lead. Never overwrite status, source, or follow-up."""
    merged = dict(existing)
    if not is_real_name(existing.get("name")) and is_real_name(incoming.get("name")):
        merged["name"] = str(incoming.get("name") or "").strip()
    for key in _CONTACT_STRINGS:
        if _blank(existing.get(key)) and not _blank(incoming.get(key)):
            merged[key] = incoming.get(key)
    if coords_of(existing) is None and coords_of(incoming) is not None:
        merged["lat"] = incoming.get("lat")
        merged["lng"] = incoming.get("lng")
    if _blank(existing.get("phone")) and not _blank(incoming.get("phone")):
        merged["phone"] = incoming.get("phone") or ""
        merged["phone_normalized"] = incoming.get("phone_normalized") or ""
    elif _blank(existing.get("phone_normalized")) and incoming.get("phone_normalized"):
        merged["phone_normalized"] = incoming.get("phone_normalized") or ""
    if _blank(existing.get("whatsapp")) and incoming.get("whatsapp"):
        merged["whatsapp"] = incoming.get("whatsapp")
    if _blank(existing.get("osm_id")) and incoming.get("osm_id"):
        merged["osm_type"] = incoming.get("osm_type") or ""
        merged["osm_id"] = str(incoming.get("osm_id") or "")
    if existing.get("rating") is None and incoming.get("rating") is not None:
        merged["rating"] = incoming.get("rating")
    return merged


def record_changed(before: dict, after: dict) -> bool:
    keys = (
        "name",
        "phone",
        "phone_normalized",
        "whatsapp",
        "email",
        "website",
        "facebook",
        "instagram",
        "address",
        "city",
        "area",
        "opening_hours",
        "notes",
        "listing_url",
        "osm_type",
        "osm_id",
        "rating",
        "lat",
        "lng",
    )
    for key in keys:
        if (before.get(key) or None) != (after.get(key) or None):
            # Treat "" and None as the same empty value for strings.
            if _blank(before.get(key)) and _blank(after.get(key)):
                continue
            return True
    return False


def _richer_text(current: object, candidate: object) -> object:
    if _blank(current):
        return candidate
    if _blank(candidate):
        return current
    if isinstance(current, str) and isinstance(candidate, str):
        if len(candidate.strip()) > len(current.strip()) + 8:
            return candidate
    return current


def combine_records(primary: dict, extra: dict) -> dict:
    """Merge two unsaved search hits into the more complete of the two."""
    combined = dict(primary)
    if is_real_name(extra.get("name")) and (
        not is_real_name(primary.get("name"))
        or len(str(extra.get("name"))) > len(str(primary.get("name") or "")) + 3
    ):
        combined["name"] = extra.get("name")
    for key in (
        "email",
        "website",
        "facebook",
        "instagram",
        "address",
        "city",
        "area",
        "opening_hours",
    ):
        combined[key] = _richer_text(combined.get(key), extra.get(key))
    phones: list[str] = []
    for source in (primary.get("phone"), extra.get("phone")):
        for part in re.split(r"\s*;\s*", str(source or "")):
            part = part.strip()
            if part and part not in phones:
                phones.append(part)
    if phones:
        combined["phone"] = "; ".join(phones)
    if _blank(combined.get("whatsapp")) and extra.get("whatsapp"):
        combined["whatsapp"] = extra.get("whatsapp")
    if _blank(combined.get("phone_normalized")) and extra.get("phone_normalized"):
        combined["phone_normalized"] = extra.get("phone_normalized")
    if coords_of(combined) is None and coords_of(extra) is not None:
        combined["lat"] = extra.get("lat")
        combined["lng"] = extra.get("lng")
    if _blank(primary.get("phone")) and extra.get("osm_id"):
        combined["osm_type"] = extra.get("osm_type") or ""
        combined["osm_id"] = extra.get("osm_id") or ""
    return combined


def collapse_duplicates(
    records: list[dict],
    country_code: str = "92",
    meters: float = 150.0,
) -> list[dict]:
    """Collapse duplicate shops inside one search result."""
    unique: list[dict] = []
    for record in records:
        match_at: int | None = None
        for index, existing in enumerate(unique):
            if is_same_lead(existing, record, country_code, meters):
                match_at = index
                break
        if match_at is None:
            unique.append(record)
        else:
            unique[match_at] = combine_records(unique[match_at], record)
    return unique


def refresh_numbers(record: dict, country_code: str = "92") -> dict:
    """Recompute phone_normalized and WhatsApp after phones were combined."""
    updated = dict(record)
    parsed = normalize_many(str(updated.get("phone") or ""), country_code)
    explicit = normalize_phone(str(updated.get("whatsapp") or ""), country_code)
    mobiles = [item for item in parsed if item.is_mobile]
    if explicit.is_mobile and explicit.normalized:
        updated["whatsapp"] = explicit.normalized
    elif mobiles:
        updated["whatsapp"] = mobiles[0].normalized or ""
    else:
        updated["whatsapp"] = ""
    if parsed:
        primary = mobiles[0] if mobiles else parsed[0]
        updated["phone"] = "; ".join(item.original.strip() for item in parsed)
        updated["phone_normalized"] = primary.normalized or ""
    return updated
