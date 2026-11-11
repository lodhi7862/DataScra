"""Phone normalisation and pasted-contact parsing."""

from app.services.phone import extract_phones, normalize_phone, parse_contact_text


def test_pakistani_mobile_formats():
    expected = "+923001234567"
    samples = [
        "03001234567",
        "0300-1234567",
        "0300 1234567",
        "+92 300 1234567",
        "+92-300-1234567",
        "0092 300 1234567",
        "923001234567",
        "3001234567",
        "+92 0300 1234567",
    ]
    for sample in samples:
        result = normalize_phone(sample)
        assert result.is_valid, sample
        assert result.normalized == expected, sample
        assert result.is_mobile, sample
        assert result.whatsapp == expected


def test_landline_is_not_whatsapp():
    result = normalize_phone("051-2345678")
    assert result.is_valid
    assert result.normalized == "+92512345678"
    assert result.is_mobile is False
    assert result.whatsapp is None

    international = normalize_phone("+92 51 2345678")
    assert international.normalized == "+92512345678"
    assert international.is_mobile is False


def test_invalid_numbers():
    for sample in ["", "abc", "123", "0000000000", "N/A", None]:
        result = normalize_phone(sample)
        assert result.is_valid is False
        assert result.normalized is None


def test_extract_from_mixed_text_ignores_coordinates():
    text = "Call 0300-1234567 or the shop line 051-2345678. Pin 33.565 73.016"
    found = extract_phones(text)
    numbers = [item.normalized for item in found]
    assert numbers == ["+923001234567", "+92512345678"]


def test_parse_facebook_paste():
    text = """
    Al-Rahim Jewellers
    Jewellery shop
    Address: Saddar Road, Rawalpindi
    0300-1234567
    https://facebook.com/alrahimjewellers
    info@alrahim.test
    """
    parsed = parse_contact_text(text)
    assert parsed["name"] == "Al-Rahim Jewellers"
    assert parsed["whatsapp"] == "+923001234567"
    assert "Saddar Road" in parsed["address"]
    assert "facebook.com/alrahimjewellers" in parsed["facebook"]
    assert parsed["email"] == "info@alrahim.test"
    assert parsed["source"] == "Facebook"
