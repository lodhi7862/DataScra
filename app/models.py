"""Shared constants and small helpers. Persistence lives in db.py."""

from __future__ import annotations

from datetime import datetime

STATUSES: list[str] = [
    "New",
    "Contacted",
    "Interested",
    "Demo Scheduled",
    "Demo Done",
    "Customer",
    "Not Interested",
    "Wrong Number",
]

SOURCES: list[str] = ["OpenStreetMap", "Facebook", "Manual", "Import"]

# Statuses that no longer need a follow-up reminder.
CLOSED_STATUSES: set[str] = {"Customer", "Not Interested", "Wrong Number"}

DEFAULT_WHATSAPP_TEMPLATE = (
    "Assalam o Alaikum {shop_name}, main {my_name} hoon. "
    "Hum jewellery stock aur sales management ka software banate hain, "
    "jis se aapka hisaab aur stock asaan ho jata hai. "
    "Kya main aapko 2 minute mein bata sakta hoon?"
)

DEFAULT_SETTINGS: dict[str, str] = {
    "country_code": "92",
    "my_name": "",
    "whatsapp_template": DEFAULT_WHATSAPP_TEMPLATE,
}

# Keyword chips on the Find Shops page. Values are Overpass/Python regexes.
CHIP_PATTERNS: dict[str, str] = {
    "jewellers": "jewel|jewell|jeweller|jewelry|jewellery",
    "gold": "gold|sona",
    "sarafa": "sarafa|saraffa",
    "goldsmith": "goldsmith",
    "ornaments": "ornament|zewar|zevar|zewaar",
}

CHIP_LABELS: dict[str, str] = {
    "jewellers": "jewellers",
    "gold": "gold",
    "sarafa": "sarafa",
    "goldsmith": "goldsmith",
    "ornaments": "ornaments",
}

# Used when the user does not pick a chip or type a keyword.
DEFAULT_NAME_REGEX = "jewel|jewell|gold|sona|sarafa|zewar|zevar|ornament"

SEARCH_TTL_HOURS = 24
GEOCODE_TTL_DAYS = 30


def now_iso() -> str:
    """Local time as an ISO string. Fine for a single-user desktop app."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def today_iso() -> str:
    return datetime.now().astimezone().date().isoformat()
