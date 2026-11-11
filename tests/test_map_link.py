"""Map links must search by shop name, not by a bare coordinate pin."""

from app.services.export import map_link


def test_named_shop_searches_by_name_and_centers_on_the_point():
    url = map_link({
        "name": "Patiala Jewellers",
        "address": "Murree Road, راولپنڈی",
        "city": "راولپنڈی",
        "lat": 33.625094,
        "lng": 73.068408,
    })
    assert url.startswith("https://www.google.com/maps/search/Patiala+Jewellers")
    assert "Murree" not in url
    assert "/@33.625094,73.068408,18z" in url
    assert "query=33.625094" not in url


def test_placeholder_name_stays_on_its_own_pin():
    url = map_link({
        "name": "Jewellery shop",
        "address": "",
        "city": "Rawalpindi",
        "lat": 33.720986,
        "lng": 73.057886,
    })
    assert url == "https://www.google.com/maps/search/jewellery/@33.720986,73.057886,19z"
    assert "Rawalpindi" not in url


def test_name_only_when_there_is_no_point():
    url = map_link({"name": "Al Khyber Jewels", "city": "Rawalpindi"})
    assert url == "https://www.google.com/maps/search/?api=1&query=Al+Khyber+Jewels%2C+Rawalpindi"


def test_street_text_stays_out_of_the_search():
    query_url = map_link({
        "name": "saj jewllers",
        "address": "اسٹریٹ 8, rawalpindi",
        "city": "Lahore",
        "lat": 31.52,
        "lng": 74.35,
    })
    assert query_url.startswith("https://www.google.com/maps/search/saj+jewllers%2C+Lahore/@31.52,74.35,18z")
    assert "8" not in query_url.split("/@")[0]


def test_known_place_link_is_kept():
    place = "https://www.google.com/maps/place/SAJ+Designer+Jewellery/@33.586696,73.0875286,17z/data=!4m2!3m1!1s0x38df9352b261b27d:0xe5ec65511482eaae"
    url = map_link({
        "name": "saj jewllers",
        "city": "rawalpindi",
        "lat": 33.586666,
        "lng": 73.087475,
        "listing_url": place,
    })
    assert url == place
