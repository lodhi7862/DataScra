"""Dashboard counts and follow-ups."""

from __future__ import annotations

from fastapi import APIRouter

from app.services.leads import dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def get_dashboard() -> dict:
    return dashboard()
