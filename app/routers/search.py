"""Find-shops search."""

from __future__ import annotations

import time

from fastapi import APIRouter

from app.schemas import ListingRequest, SearchRequest
from app.services.leads import annotate_saved
from app.services.listings import enrich_shops
from app.services.osm import search_shops

router = APIRouter(tags=["search"])


@router.post("/search")
async def search(body: SearchRequest) -> dict:
    started = time.perf_counter()
    result = await search_shops(
        body.city,
        body.chips,
        body.keyword,
        body.area,
        refresh=body.refresh,
    )
    annotate_saved(result["shops"])
    result["count"] = len(result["shops"])
    result["took_ms"] = int((time.perf_counter() - started) * 1000)
    return result


@router.post("/listings")
async def listings(body: ListingRequest) -> dict:
    return await enrich_shops([shop.model_dump() for shop in body.shops])
