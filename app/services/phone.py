"""Parse and normalise phone numbers.

Pakistan (+92) is the default. Mobiles (03xx…) are treated as WhatsApp-capable.
The original text is always kept so the CRM can show what was typed or scraped
from a public directory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\+|00)?\d(?:[\d\s().\-]{5,22})\d(?!\d)")
_ADDRESS_HINT = re.compile(
    r"\b(road|rd\.?|street|st\.?|town|sector|market|plaza|bazar|bazaar|"
    r"chowk|block|phase|avenue|ave\.?|lane|mohalla|colony|nagar|abad|"
    r"saddar|satellite|cantt|scheme)\b",
    re.IGNORECASE,
)
_GENERIC_NAMES = {
    "jewellery",
    "jewelry",
    "jeweller",
    "jewellers",
    "jewellery shop",
    "jewelry shop",
    "goldsmith",
    "page",
    "home",
    "about",
}


@dataclass(frozen=True)
class PhoneResult:
    original: str
    normalized: str | None
    is_valid: bool
    is_mobile: bool

    @property
    def whatsapp(self) -> str | None:
        if self.is_valid and self.is_mobile and self.normalized:
            return self.normalized
        return None


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _clean(value: str | None) -> str:
    text = (value or "").replace("\u00a0", " ").replace("–", "-").replace("—", "-")
    text = text.strip()
    text = re.sub(r"(?:ext\.?|extension)\s*\d+\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def normalize_phone(raw: str | None, country_code: str = "92") -> PhoneResult:
    """Return E.164 (+92300…) plus whether the number is a Pakistani mobile.

    Landlines stay in international form but are not marked WhatsApp-capable.
    """
    original = _clean(raw)
    cc = only_digits(country_code) or "92"
    invalid = PhoneResult(original, None, False, False)
    if not original:
        return invalid
    # More than one dot is almost always a coordinate, not a phone number.
    if original.count(".") > 1:
        return invalid
    if "." in original and not re.match(r"(\+|00|0)", original):
        return invalid

    digits = only_digits(original)
    if len(digits) < 8 or len(digits) > 15:
        return invalid

    explicit_intl = original.startswith("+") or digits.startswith("00")
    if original.startswith("+"):
        intl = digits
    elif digits.startswith("00"):
        intl = digits[2:]
    elif digits.startswith("0"):
        intl = cc + digits[1:]
    elif digits.startswith(cc) and len(digits) >= len(cc) + 8:
        intl = digits
    elif cc == "92" and re.fullmatch(r"3\d{9}", digits):
        intl = "92" + digits
    else:
        return invalid

    if not intl.startswith(cc):
        # A number the user wrote with + or 00 and a different country code.
        if explicit_intl and 10 <= len(intl) <= 15 and len(set(intl)) > 1:
            return PhoneResult(original, f"+{intl}", True, False)
        return invalid

    national = intl[len(cc) :]
    if national.startswith("0"):
        national = national[1:]
        intl = cc + national

    if len(set(national)) <= 1:
        return invalid

    is_mobile = bool(cc == "92" and re.fullmatch(r"3\d{9}", national))
    if cc == "92":
        if is_mobile:
            pass
        elif not 8 <= len(national) <= 10:
            return invalid
    elif not 8 <= len(national) <= 12:
        return invalid

    if not 10 <= len(intl) <= 15:
        return invalid
    return PhoneResult(original, f"+{intl}", True, is_mobile)


def extract_phones(text: str, country_code: str = "92") -> list[PhoneResult]:
    """Find phone numbers inside free text (a Facebook paste, a note, …)."""
    seen: set[str] = set()
    found: list[PhoneResult] = []
    for match in _CANDIDATE_RE.finditer(text or ""):
        result = normalize_phone(match.group(0), country_code)
        if result.is_valid and result.normalized and result.normalized not in seen:
            seen.add(result.normalized)
            found.append(result)
    return found


def normalize_many(raw: str | None, country_code: str = "92") -> list[PhoneResult]:
    """Normalise a field that may hold several numbers separated by ; | / or commas."""
    text = _clean(raw)
    if not text:
        return []
    parts = re.split(r"[;|/]|,(?=\s*\+?\d)", text)
    seen: set[str] = set()
    results: list[PhoneResult] = []
    for part in parts:
        extracted = extract_phones(part, country_code)
        if not extracted:
            one = normalize_phone(part, country_code)
            extracted = [one] if one.is_valid else []
        for item in extracted:
            if item.normalized and item.normalized not in seen:
                seen.add(item.normalized)
                results.append(item)
    return results


def choose_primary(results: list[PhoneResult]) -> PhoneResult | None:
    """Prefer a mobile so WhatsApp and the main phone agree when both exist."""
    valid = [item for item in results if item.is_valid and item.normalized]
    mobiles = [item for item in valid if item.is_mobile]
    if mobiles:
        return mobiles[0]
    return valid[0] if valid else None


def _ensure_scheme(url: str) -> str:
    url = url.strip().rstrip(".,)")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return "https://" + url.lstrip("/")


def _is_generic_name(line: str) -> bool:
    return line.strip().casefold() in _GENERIC_NAMES


def parse_contact_text(text: str, country_code: str = "92") -> dict[str, str]:
    """Pull a name, phones, links, and an address out of pasted page text.

    This does not fetch Facebook. It only reads text the user copied themselves.
    """
    raw = (text or "").replace("\u00a0", " ").strip()
    phones = extract_phones(raw, country_code)
    emails = _EMAIL_RE.findall(raw)
    urls = [item.rstrip(".,)") for item in _URL_RE.findall(raw)]

    fb_bare = re.search(
        r"(?:https?://)?(?:www\.)?(?:facebook|fb)\.com/[\w.\-/%]+",
        raw,
        flags=re.IGNORECASE,
    )
    if fb_bare and not any("facebook.com" in u.lower() or "fb.com" in u.lower() for u in urls):
        urls.append(_ensure_scheme(fb_bare.group(0)))
    ig_bare = re.search(
        r"(?:https?://)?(?:www\.)?instagram\.com/[\w.\-/%]+",
        raw,
        flags=re.IGNORECASE,
    )
    if ig_bare and not any("instagram.com" in u.lower() for u in urls):
        urls.append(_ensure_scheme(ig_bare.group(0)))

    def pick(predicate) -> str:
        for url in urls:
            if predicate(url.casefold()):
                return _ensure_scheme(url)
        return ""

    facebook = pick(lambda url: "facebook.com" in url or "fb.com" in url)
    instagram = pick(lambda url: "instagram.com" in url)
    website = pick(
        lambda url: "facebook.com" not in url
        and "fb.com" not in url
        and "instagram.com" not in url
        and "wa.me" not in url
        and "whatsapp.com" not in url
    )

    mobile = next((item for item in phones if item.is_mobile), None)
    phone_display = "; ".join(item.original.strip() for item in phones)

    content_lines: list[str] = []
    address_lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip(" \t-•")
        if not line:
            continue
        lowered = line.casefold()
        if lowered.startswith("address"):
            rest = re.sub(r"^address\s*[:\-]\s*", "", line, count=1, flags=re.IGNORECASE).strip()
            if rest:
                address_lines.append(rest)
            continue
        if re.fullmatch(r"(phone|mobile|whatsapp|email|website|call|tel)\s*:?", lowered):
            continue
        if _EMAIL_RE.search(line) or _URL_RE.search(line):
            continue
        if "facebook.com" in lowered or "instagram.com" in lowered or "fb.com" in lowered:
            continue
        if extract_phones(line, country_code):
            continue
        content_lines.append(line)

    name = ""
    rest_lines = content_lines
    if content_lines:
        if _is_generic_name(content_lines[0]) and len(content_lines) > 1:
            name = content_lines[1]
            rest_lines = content_lines[2:]
        else:
            name = content_lines[0]
            rest_lines = content_lines[1:]

    notes: list[str] = []
    for line in rest_lines:
        if _ADDRESS_HINT.search(line):
            address_lines.append(line)
        else:
            notes.append(line)

    return {
        "name": name[:200],
        "phone": phone_display[:300],
        "whatsapp": mobile.normalized if mobile else "",
        "email": emails[0] if emails else "",
        "website": website[:500],
        "facebook": facebook[:500],
        "instagram": instagram[:500],
        "address": ", ".join(address_lines)[:500],
        "notes": "\n".join(notes)[:2000],
        "source": "Facebook" if facebook else "Manual",
    }
