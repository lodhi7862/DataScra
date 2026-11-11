"""Offline checks for city ranking and the Overpass query."""

import re

import pytest

from app.errors import AppError
from app.services.nominatim import choose_place
from app.services.osm import _keep, build_name_regex, build_overpass_query


def test_choose_place_prefers_the_city_relation():
    results = [
        {
            "type": "suburb",
            "category": "place",
            "importance": 0.45,
            "osm_type": "node",
            "osm_id": 11,
            "lat": "33.60",
            "lon": "73.05",
            "display_name": "Saddar, Rawalpindi, Punjab, Pakistan",
            "boundingbox": ["33.59", "33.61", "73.04", "73.06"],
        },
        {
            "type": "administrative",
            "category": "boundary",
            "addresstype": "city",
            "importance": 0.55,
            "osm_type": "relation",
            "osm_id": 357020,
            "lat": "33.60",
            "lon": "73.04",
            "display_name": "Rawalpindi, Punjab, Pakistan",
            "boundingbox": ["33.45", "33.72", "72.90", "73.20"],
        },
    ]
    place = choose_place(results, "Rawalpindi")
    assert place["osm_type"] == "relation"
    assert place["osm_id"] == 357020
    assert place["area_id"] == 3600000000 + 357020
    assert place["label"].startswith("Rawalpindi")


def test_overpass_query_uses_area_or_bbox():
    area_query = build_overpass_query("jewel|gold", 3600357020, None)
    assert "area(3600357020)->.searchArea;" in area_query
    assert 'nwr["shop"="jewelry"](area.searchArea);' in area_query
    assert "out center tags;" in area_query
    bbox_query = build_overpass_query("jewel", None, [33.4, 33.8, 72.8, 73.2])
    assert "(33.4,72.8,33.8,73.2)" in bbox_query


def test_keyword_regex_and_rejection():
    assert "sarafa" in build_name_regex(["sarafa"], "")
    assert "zewar" in build_name_regex([], "zewar, sona")
    assert build_name_regex([], "jewel|gold") == "jewel|gold"
    with pytest.raises(AppError):
        build_name_regex([], 'jewel"; out;')


def test_name_match_skips_unrelated_shops():
    pattern = re.compile(build_name_regex(["gold", "jewellers"], ""), re.IGNORECASE)
    assert _keep({"shop": "jewelry", "name": "ABC"}, pattern)
    assert _keep({"craft": "goldsmith", "name": "Haji"}, pattern)
    assert _keep({"shop": "yes", "name": "Sona Sarafa"}, pattern)
    assert _keep({"shop": "yes", "name": "City Bakery"}, pattern) is False
    assert _keep({"shop": "yes", "name": "Sona Gold"}, pattern)
    assert _keep({"shop": "sports", "name": "Gold Gym"}, pattern) is False
