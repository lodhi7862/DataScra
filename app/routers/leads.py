"""Lead CRUD, quick paste, and bulk actions."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.db import country_code
from app.schemas import BulkAction, LeadCreate, LeadUpdate, ParseRequest, SaveShopsRequest
from app.services.leads import (
    create_lead,
    delete_leads,
    get_lead,
    list_leads,
    save_shops,
    set_status,
    update_lead,
)
from app.services.phone import parse_contact_text

router = APIRouter(tags=["leads"])


@router.get("/leads")
def get_leads(
    q: str = "",
    city: str = "",
    status: str = "",
    source: str = "",
    has_phone: str = "",
    has_whatsapp: str = "",
    sort: str = "created_at",
    direction: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=10, le=100),
) -> dict:
    return list_leads(
        q=q,
        city=city,
        status=status,
        source=source,
        has_phone=has_phone,
        has_whatsapp=has_whatsapp,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )


@router.post("/leads")
def post_lead(body: LeadCreate) -> dict:
    return create_lead(body.model_dump())


@router.post("/leads/save")
def post_save(body: SaveShopsRequest) -> dict:
    return save_shops([shop.model_dump() for shop in body.shops])


@router.post("/leads/bulk")
def post_bulk(body: BulkAction) -> dict:
    if body.action == "delete":
        return {"deleted": delete_leads(body.ids)}
    return {"updated": set_status(body.ids, body.status or "")}


@router.get("/leads/{lead_id}")
def read_lead(lead_id: int) -> dict:
    return get_lead(lead_id)


@router.patch("/leads/{lead_id}")
def patch_lead(lead_id: int, body: LeadUpdate) -> dict:
    return update_lead(lead_id, body.model_dump(exclude_unset=True))


@router.delete("/leads/{lead_id}")
def remove_lead(lead_id: int) -> dict:
    deleted = delete_leads([lead_id])
    if not deleted:
        from app.errors import AppError

        raise AppError("That lead was not found.", status_code=404)
    return {"deleted": deleted}


@router.post("/parse-text")
def parse_text(body: ParseRequest) -> dict:
    return parse_contact_text(body.text, country_code())
