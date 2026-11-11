"""Settings: calling code, your name, and the WhatsApp template."""

from __future__ import annotations

from fastapi import APIRouter

from app.db import save_settings
from app.schemas import SettingsUpdate
from app.services.leads import settings_public

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings() -> dict:
    return settings_public()


@router.put("/settings")
def put_settings(body: SettingsUpdate) -> dict:
    save_settings(body.model_dump())
    return settings_public()
