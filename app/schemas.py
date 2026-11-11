"""Request bodies for the HTTP API."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import SOURCES, STATUSES


def _clean(value: str) -> str:
    return value.strip()


class SearchRequest(BaseModel):
    city: str = Field(min_length=2, max_length=120)
    keyword: str = Field(default="", max_length=80)
    chips: list[str] = Field(default_factory=list, max_length=8)
    area: str = Field(default="", max_length=80)
    refresh: bool = False

    @field_validator("city", "keyword", "area")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("chips")
    @classmethod
    def known_chips(cls, value: list[str]) -> list[str]:
        allowed = {"jewellers", "gold", "sarafa", "goldsmith", "ornaments"}
        return [chip for chip in value if chip in allowed]


class ListingShop(BaseModel):
    name: str = ""
    phone: str = ""
    whatsapp: str = ""
    website: str = ""
    address: str = ""
    city: str = ""
    area: str = ""
    opening_hours: str = ""
    lat: float | None = None
    lng: float | None = None
    rating: float | None = None

    @field_validator("name", "phone", "whatsapp", "website", "address", "city", "area", "opening_hours")
    @classmethod
    def strip_listing(cls, value: str) -> str:
        return value.strip()


class ListingRequest(BaseModel):
    shops: list[ListingShop] = Field(min_length=1, max_length=6)


class ShopIn(BaseModel):
    name: str = ""
    phone: str = ""
    whatsapp: str = ""
    email: str = ""
    website: str = ""
    facebook: str = ""
    instagram: str = ""
    address: str = ""
    city: str = ""
    area: str = ""
    lat: float | None = None
    lng: float | None = None
    opening_hours: str = ""
    listing_url: str = ""
    osm_type: str = ""
    osm_id: str = ""
    source: str = "OpenStreetMap"
    rating: float | None = Field(default=None, ge=0, le=5)

    @field_validator(
        "name",
        "phone",
        "whatsapp",
        "email",
        "website",
        "facebook",
        "instagram",
        "address",
        "city",
        "area",
        "opening_hours",
        "listing_url",
        "osm_type",
        "osm_id",
        "source",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class SaveShopsRequest(BaseModel):
    shops: list[ShopIn] = Field(min_length=1, max_length=400)


class LeadCreate(BaseModel):
    name: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=300)
    whatsapp: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=500)
    facebook: str = Field(default="", max_length=500)
    instagram: str = Field(default="", max_length=500)
    address: str = Field(default="", max_length=500)
    city: str = Field(default="", max_length=120)
    area: str = Field(default="", max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    rating: float | None = Field(default=None, ge=0, le=5)
    source: str = "Manual"
    status: str = "New"
    notes: str = Field(default="", max_length=5000)
    follow_up_date: str = ""
    opening_hours: str = Field(default="", max_length=300)
    listing_url: str = Field(default="", max_length=500)

    @field_validator(
        "name",
        "phone",
        "whatsapp",
        "email",
        "website",
        "facebook",
        "instagram",
        "address",
        "city",
        "area",
        "notes",
        "opening_hours",
        "listing_url",
        "follow_up_date",
        "source",
        "status",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str) -> str:
        if value not in STATUSES:
            raise ValueError("Choose a valid status.")
        return value

    @field_validator("source")
    @classmethod
    def manual_source(cls, value: str) -> str:
        if value not in {"Manual", "Facebook"}:
            return "Manual"
        return value

    @field_validator("follow_up_date")
    @classmethod
    def iso_date(cls, value: str) -> str:
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Follow-up date must be YYYY-MM-DD.")
        return value

    @model_validator(mode="after")
    def has_identity(self) -> "LeadCreate":
        if not (self.name or self.phone or self.whatsapp):
            raise ValueError("Enter a shop name or a phone number.")
        return self


class LeadUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=300)
    whatsapp: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    facebook: str | None = Field(default=None, max_length=500)
    instagram: str | None = Field(default=None, max_length=500)
    address: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=120)
    area: str | None = Field(default=None, max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    rating: float | None = Field(default=None, ge=0, le=5)
    source: str | None = None
    status: str | None = None
    notes: str | None = Field(default=None, max_length=5000)
    follow_up_date: str | None = None
    opening_hours: str | None = Field(default=None, max_length=300)
    listing_url: str | None = Field(default=None, max_length=500)

    @field_validator(
        "name",
        "phone",
        "whatsapp",
        "email",
        "website",
        "facebook",
        "instagram",
        "address",
        "city",
        "area",
        "notes",
        "opening_hours",
        "listing_url",
        "follow_up_date",
        "source",
        "status",
    )
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str | None) -> str | None:
        if value is not None and value not in STATUSES:
            raise ValueError("Choose a valid status.")
        return value

    @field_validator("source")
    @classmethod
    def known_source(cls, value: str | None) -> str | None:
        if value is not None and value not in SOURCES:
            raise ValueError("Choose a valid source.")
        return value

    @field_validator("follow_up_date")
    @classmethod
    def iso_date(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Follow-up date must be YYYY-MM-DD.")
        return value


class BulkAction(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    action: str
    status: str | None = None

    @field_validator("action")
    @classmethod
    def known_action(cls, value: str) -> str:
        if value not in {"status", "delete"}:
            raise ValueError("Unknown bulk action.")
        return value

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str | None) -> str | None:
        if value is not None and value not in STATUSES:
            raise ValueError("Choose a valid status.")
        return value

    @model_validator(mode="after")
    def status_required(self) -> "BulkAction":
        if self.action == "status" and not self.status:
            raise ValueError("Choose a status.")
        return self


class ParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class SettingsUpdate(BaseModel):
    country_code: str = "92"
    my_name: str = Field(default="", max_length=80)
    whatsapp_template: str = Field(default="", max_length=1000)

    @field_validator("country_code")
    @classmethod
    def calling_code(cls, value: str) -> str:
        digits = re.sub(r"\D", "", value or "")
        if not 1 <= len(digits) <= 3:
            raise ValueError("Country code must be 1 to 3 digits, for example 92.")
        return digits

    @field_validator("my_name", "whatsapp_template")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ImportCommit(BaseModel):
    token: str = Field(min_length=8, max_length=80)
    mapping: dict[str, str]
    source: str = "Import"

    @field_validator("source")
    @classmethod
    def known_source(cls, value: str) -> str:
        if value not in SOURCES:
            return "Import"
        return value
