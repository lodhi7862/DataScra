"""Google listing parsing. These tests use a small fake payload, not the network."""

from app.services.listings import listing_name_match, merge_listing, nearest_place, parse_listing_payload


def _place(name: str, lat: float, lng: float, phone: str) -> list:
    return [
        "0x38df94c3147bc3bf:0x682aa0c515ebbdca",
        name,
        "Mohalla",
        "Shop # 7, Parsi market, Murree Rd, Mohalla, Rawalpindi, 46000, Pakistan",
        [None, None, lat, lng],
        [None, None, None, None, 4.4, 388, "388 reviews"],
        ["Monday", 1, [2026, 11, 16], [["12:30–8:30 PM"]]],
        ["Tuesday", 2, [2026, 11, 17], [["12:30–8:30 PM"]]],
        ["Wednesday", 3, [2026, 11, 18], [["12:30–8:30 PM"]]],
        ["Thursday", 4, [2026, 11, 19], [["12:30–8:30 PM"]]],
        ["Friday", 5, [2026, 11, 20], [["12:30–8:30 PM"]]],
        ["Saturday", 6, [2026, 11, 21], [["12:30–8:30 PM"]]],
        ["Sunday", 7, [2026, 11, 22], [["12:30–8:30 PM"]]],
        [6, 1, ["https://example.com/call_googblue_24dp.png", phone]],
        [7, 2, ["https://example.com/public_googblue_24dp.png", "https://patiala.example/shop"]],
    ]


def test_unnamed_pin_uses_the_closest_shop_and_its_phone():
    payload = [
        [
            "0x1111111111111111:0x2222222222222222",
            "Far Jewellers",
            [None, None, 33.73, 73.07],
            ["+92 300 1111111", [["0300 1111111", 1]]],
        ],
        [
            "0x3333333333333333:0x4444444444444444",
            "Gold Bank",
            "Murree Road, Rawalpindi",
            [None, None, 33.72099, 73.05790],
            ["+92 316 7067088", [["0316 7067088", 1], ["+92 316 7067088", 2]]],
        ],
    ]
    shop = {"name": "Jewellery shop", "city": "Rawalpindi", "lat": 33.720986, "lng": 73.057886, "phone": ""}
    details = nearest_place(payload, (shop["lat"], shop["lng"]))
    assert details is not None
    assert details["name"] == "Gold Bank"
    assert details["phone"] == "+92 316 7067088"
    updates = merge_listing(shop, details, "92")
    assert updates["name"] == "Gold Bank"
    assert updates["whatsapp"] == "+923167067088"
    assert listing_name_match("Patiala Jewelers", "Patiala Jewellers")
    assert listing_name_match("dulhan jewlery", "Dulhan Jewellery House")
    assert listing_name_match("saj jewllers", "Saj Jewellers")
    assert listing_name_match("Zee.sy", "Zee.Sy Islamabad (Giga Boutique Mall) - Artificial Jewellery")
    assert not listing_name_match("Gold", "Gold Star Jewelers")


def test_phone_without_a_call_icon_is_kept_when_the_pin_is_a_few_kilometres_off():
    payload = [
        [
            "0x38dfed29cfd32597:0x6f60f79aa64034ba",
            "Zee.Sy Islamabad (Giga Boutique Mall) - Artificial Jewellery",
            "Shop# 5, 1st floor, Giga Boutique Mall, Main GT Rd, Islamabad, 44000, Pakistan",
            [None, None, 33.5322152, 73.1322649],
            ["+92 321 3800037", [["0321 3800037", 1], ["+92 321 3800037", 2]]],
        ]
    ]
    shop = {"name": "Zee.sy", "city": "Rawalpindi", "lat": 33.52168, "lng": 73.155857}
    details = parse_listing_payload(payload, shop)
    assert details is not None
    assert details["phone"] == "+92 321 3800037"
    assert details["address"].startswith("Shop# 5")


def test_parse_picks_the_nearby_listing_and_reads_its_details():
    payload = [
        _place("Patiala Jewellers", 31.5, 74.3, "+92 42 1111111"),
        _place("Patiala Jewellers", 33.6252177, 73.0684927, "+92 51 5761435"),
    ]
    shop = {
        "name": "Patiala Jewelers",
        "address": "Murree Road, راولپنڈی",
        "city": "Rawalpindi",
        "lat": 33.625094,
        "lng": 73.068408,
    }
    details = parse_listing_payload(payload, shop)
    assert details is not None
    assert details["phone"] == "+92 51 5761435"
    assert details["address"].startswith("Shop # 7")
    assert details["website"] == "https://patiala.example/shop"
    assert details["opening_hours"] == "Daily 12:30-8:30 PM"
    assert details["rating"] == 4.4
    assert details["lat"] == 33.6252177
    assert details["area"] == "Mohalla"


def test_a_far_listing_is_not_used():
    payload = [_place("Patiala Jewellers", 31.5, 74.3, "+92 42 1111111")]
    shop = {"name": "Patiala Jewellers", "lat": 33.625094, "lng": 73.068408}
    assert parse_listing_payload(payload, shop) is None


def test_merge_fills_a_blank_phone_and_keeps_one_already_stored():
    details = {
        "phone": "+92 51 5761435",
        "address": "Shop # 7, Parsi market, Murree Rd, Rawalpindi, 46000, Pakistan",
        "opening_hours": "Daily 12:30-8:30 PM",
        "rating": 4.4,
        "website": "https://patiala.example/shop",
        "lat": 33.6252177,
        "lng": 73.0684927,
        "area": "Mohalla",
    }
    blank = {"name": "Patiala Jewellers", "phone": "", "address": "Murree Road"}
    updates = merge_listing(blank, details, "92")
    assert updates["phone"] == "+92 51 5761435"
    assert "whatsapp" not in updates
    assert updates["address"].startswith("Shop # 7")
    assert updates["opening_hours"].startswith("Daily")

    kept = {"name": "Patiala Jewellers", "phone": "051-1111111", "address": "Murree Road"}
    again = merge_listing(kept, details, "92")
    assert "phone" not in again


def test_close_pin_matches_a_longer_title_and_opens_that_place():
    place_id = "0x38df9352b261b27d:0xe5ec65511482eaae"
    payload = [
        [
            place_id,
            "SAJ Designer Jewellery",
            [None, None, 33.586696, 73.0875286],
            ["+92 331 5081221", [["0331 5081221", 1], ["+92 331 5081221", 2]]],
        ]
    ]
    shop = {
        "name": "saj jewllers",
        "address": "اسٹریٹ 8, rawalpindi",
        "city": "rawalpindi",
        "lat": 33.586666,
        "lng": 73.087475,
        "phone": "",
    }
    details = parse_listing_payload(payload, shop)
    assert details is not None
    assert details["name"] == "SAJ Designer Jewellery"
    assert details["phone"] == "+92 331 5081221"
    assert place_id in details["listing_url"]
    updates = merge_listing(shop, details, "92")
    assert updates["phone"] == "+92 331 5081221"
    assert updates["whatsapp"] == "+923315081221"
    assert updates["name"] == "SAJ Designer Jewellery"
    assert updates["listing_url"] == details["listing_url"]
    far = {"name": "saj jewllers", "lat": 33.60, "lng": 73.10, "phone": ""}
    assert parse_listing_payload(payload, far) is None


def test_single_listing_for_a_specific_name_can_sit_further_from_the_pin():
    place_id = "0x38df95c2f3f81803:0xc74a12d0514da91"
    payload = [
        [
            place_id,
            "Gold Test Laboratory Software",
            [None, None, 33.6164225, 73.0584358],
            ["+92 314 9868309", [["0314 9868309", 1]]],
        ]
    ]
    shop = {
        "name": "Gold Mark Gold Testing Lab",
        "city": "RAWALPINDI",
        "lat": 33.72048,
        "lng": 73.056462,
        "phone": "",
    }
    details = parse_listing_payload(payload, shop)
    assert details is not None
    assert details["phone"] == "+92 314 9868309"
    assert place_id in details["listing_url"]
    updates = merge_listing(shop, details, "92")
    assert "name" not in updates
    assert updates["whatsapp"] == "+923149868309"


def test_a_single_unrelated_listing_is_not_attached():
    payload = [
        [
            "0x1111111111111111:0x2222222222222222",
            "Pizza Hut",
            [None, None, 33.6164225, 73.0584358],
            ["+92 300 1111111", [["0300 1111111", 1]]],
        ]
    ]
    shop = {"name": "Gold Mark Gold Testing Lab", "lat": 33.72048, "lng": 73.056462}
    assert parse_listing_payload(payload, shop) is None


def test_extra_words_match_near_the_pin_in_any_city_and_ignore_a_closer_stranger():
    payload = [
        [
            "0x1111111111111111:0xaaaaaaaaaaaaaaaa",
            "City Bakery",
            [None, None, 31.5200, 74.3587],
            ["+92 42 0000000", [["042 0000000", 1]]],
        ],
        [
            "0x2222222222222222:0xbbbbbbbbbbbbbbbb",
            "Khan and Sons Jewellers",
            [None, None, 31.5216, 74.3587],
            ["+92 42 1112223", [["042 1112223", 1]]],
        ],
    ]
    shop = {"name": "Khan Jewellers", "city": "Lahore", "address": "گلی 4", "lat": 31.5200, "lng": 74.3587, "phone": ""}
    details = parse_listing_payload(payload, shop)
    assert details is not None
    assert details["name"] == "Khan and Sons Jewellers"
    assert details["phone"] == "+92 42 1112223"
    assert "0x2222222222222222:0xbbbbbbbbbbbbbbbb" in details["listing_url"]
    far = {"name": "Khan Jewellers", "city": "Lahore", "lat": 31.55, "lng": 74.40}
    assert parse_listing_payload(payload, far) is None


def test_a_generic_word_does_not_claim_a_different_shop():
    payload = [
        [
            "0x3333333333333333:0xcccccccccccccccc",
            "Gold Star Jewelers",
            [None, None, 24.8607, 67.0011],
            ["+92 21 1111111", [["021 1111111", 1]]],
        ]
    ]
    shop = {"name": "Gold", "city": "Karachi", "lat": 24.8608, "lng": 67.0011}
    assert parse_listing_payload(payload, shop) is None
