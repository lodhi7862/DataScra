"""Shared HTTP client.

Nominatim's usage policy requires a real User-Agent that identifies the
application. The same header is sent to Overpass.
"""

from __future__ import annotations

import httpx

USER_AGENT = "JewelLeads/1.0 (local jewellery-shop lead finder; personal use)"

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=15.0),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
