"""Download the current lead list as Excel or CSV."""

from __future__ import annotations

import re

from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.models import today_iso
from app.services.export import build_csv, build_workbook
from app.services.leads import list_leads

router = APIRouter(tags=["export"])


def _ids(raw: str) -> list[int] | None:
    found = [int(part) for part in raw.split(",") if part.strip().isdigit()]
    return found or None


def _slug(city: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", city.casefold()).strip("-")
    return slug or "leads"


def _load(q: str, city: str, status: str, source: str, has_phone: str, has_whatsapp: str, ids: str, sort: str, direction: str) -> list[dict]:
    result = list_leads(
        q=q,
        city=city,
        status=status,
        source=source,
        has_phone=has_phone,
        has_whatsapp=has_whatsapp,
        sort=sort,
        direction=direction,
        ids=_ids(ids),
        paginate=False,
    )
    return result["items"]


@router.get("/export/xlsx")
def export_xlsx(
    q: str = "",
    city: str = "",
    status: str = "",
    source: str = "",
    has_phone: str = "",
    has_whatsapp: str = "",
    ids: str = "",
    sort: str = "created_at",
    direction: str = "desc",
) -> Response:
    rows = _load(q, city, status, source, has_phone, has_whatsapp, ids, sort, direction)
    filename = f"jewelleads-{_slug(city)}-{today_iso()}.xlsx"
    return Response(
        content=build_workbook(rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/csv")
def export_csv(
    q: str = "",
    city: str = "",
    status: str = "",
    source: str = "",
    has_phone: str = "",
    has_whatsapp: str = "",
    ids: str = "",
    sort: str = "created_at",
    direction: str = "desc",
) -> Response:
    rows = _load(q, city, status, source, has_phone, has_whatsapp, ids, sort, direction)
    filename = f"jewelleads-{_slug(city)}-{today_iso()}.csv"
    return Response(
        content=build_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
