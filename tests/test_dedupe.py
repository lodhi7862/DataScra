"""De-duplication and merge rules."""

from app.services.dedupe import (
    collapse_duplicates,
    is_same_lead,
    merge_into_existing,
    names_similar,
)


def test_phone_match_ignores_name_and_distance():
    left = {"name": "Ali Jewellers", "phone": "03001234567", "lat": 33.6, "lng": 73.0, "city": "Rawalpindi"}
    right = {"name": "Completely different", "phone": "+92 300 1234567", "lat": 24.8, "lng": 67.0, "city": "Karachi"}
    assert is_same_lead(left, right)


def test_similar_name_within_200_metres():
    left = {"name": "Ali Jewellers", "lat": 33.6000, "lng": 73.0000, "city": "Rawalpindi"}
    near = {"name": "Ali Jewellers Rawalpindi", "lat": 33.6004, "lng": 73.0000, "city": "Rawalpindi"}
    far = {"name": "Ali Jewellers", "lat": 33.6500, "lng": 73.0500, "city": "Rawalpindi"}
    assert names_similar(left["name"], near["name"])
    assert is_same_lead(left, near)
    assert is_same_lead(left, far) is False


def test_exact_name_same_city_without_coordinates():
    left = {"name": "Noor Jewellers", "city": "Rawalpindi"}
    right = {"name": "noor jewellers", "city": "rawalpindi"}
    other = {"name": "Noor Jewellers", "city": "Lahore"}
    assert is_same_lead(left, right)
    assert is_same_lead(left, other) is False


def test_different_shops_in_a_bazaar_stay_separate():
    left = {"name": "Haji Gold Centre", "lat": 33.60, "lng": 73.01, "city": "Rawalpindi"}
    right = {"name": "Haji Gold House", "lat": 33.6002, "lng": 73.0102, "city": "Rawalpindi"}
    assert is_same_lead(left, right) is False


def test_merge_fills_gaps_and_keeps_crm_fields():
    existing = {
        "name": "Ali Jewellers",
        "phone": "03001234567",
        "phone_normalized": "+923001234567",
        "whatsapp": "+923001234567",
        "email": "",
        "website": "",
        "address": "Bank Road",
        "city": "Rawalpindi",
        "notes": "Asked for a demo",
        "status": "Interested",
        "source": "Manual",
        "follow_up_date": "2026-12-01",
        "osm_id": "",
        "rating": None,
        "lat": None,
        "lng": None,
    }
    incoming = {
        "name": "Jewellery shop",
        "phone": "03001234567",
        "phone_normalized": "+923001234567",
        "email": "ali@example.com",
        "website": "https://ali.example",
        "address": "Bank Road, Saddar, Rawalpindi",
        "notes": "from the map",
        "status": "New",
        "source": "OpenStreetMap",
        "lat": 33.6,
        "lng": 73.0,
        "osm_type": "node",
        "osm_id": "99",
    }
    merged = merge_into_existing(existing, incoming)
    assert merged["status"] == "Interested"
    assert merged["notes"] == "Asked for a demo"
    assert merged["source"] == "Manual"
    assert merged["follow_up_date"] == "2026-12-01"
    assert merged["name"] == "Ali Jewellers"
    assert merged["email"] == "ali@example.com"
    assert merged["website"] == "https://ali.example"
    assert merged["address"] == "Bank Road"
    assert merged["lat"] == 33.6
    assert merged["osm_id"] == "99"


def test_placeholder_name_is_replaced():
    existing = {"name": "Jewellery shop", "email": "", "notes": "", "phone": "", "status": "New"}
    incoming = {"name": "Sona Jewellers", "email": "", "notes": "", "phone": ""}
    merged = merge_into_existing(existing, incoming)
    assert merged["name"] == "Sona Jewellers"
    assert merged["status"] == "New"


def test_collapse_combines_phone_duplicates():
    shops = [
        {"name": "Ali Jewellers", "phone": "03001234567", "email": "", "lat": 33.6, "lng": 73.0, "city": "Rawalpindi"},
        {"name": "Ali", "phone": "+92 300 1234567", "email": "ali@example.com", "lat": 33.6, "lng": 73.0, "city": "Rawalpindi"},
    ]
    collapsed = collapse_duplicates(shops)
    assert len(collapsed) == 1
    assert collapsed[0]["email"] == "ali@example.com"
