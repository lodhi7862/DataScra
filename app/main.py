"""JewelLeads web app. Run: python -m app"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.auth import AuthMiddleware, https_only, router as auth_router, secret_key
from app.db import database_label, init_db
from app.errors import AppError
from app.routers import dashboard, export, imports, leads, search, settings
from app.services import http as http_service

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("jewelleads")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    logger.info("JewelLeads ready (%s)", database_label())
    yield
    await http_service.aclose()


app = FastAPI(title="JewelLeads", version=__version__, lifespan=lifespan)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=secret_key(),
    session_cookie="jewelleads",
    max_age=14 * 24 * 60 * 60,
    same_site="lax",
    https_only=https_only(),
)
app.include_router(auth_router)
app.include_router(search.router, prefix="/api")
app.include_router(leads.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(export.router, prefix="/api")


class _FreshStatic(StaticFiles):
    """Avoid a stale app.js while developing with --reload."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@app.exception_handler(AppError)
async def app_error(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    parts: list[str] = []
    for error in exc.errors():
        location = [str(item) for item in error.get("loc", []) if item not in {"body", "query"}]
        message = error.get("msg", "Invalid value")
        message = str(message).removeprefix("Value error, ")
        parts.append(f"{' '.join(location)}: {message}" if location else message)
    return JSONResponse(status_code=422, content={"detail": "; ".join(parts) or "Check the form and try again."})


@app.exception_handler(Exception)
async def unexpected(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong. Please try again."})


@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": "JewelLeads", "version": __version__}


@app.get("/")
def index() -> FileResponse:
    response = FileResponse(STATIC_DIR / "index.html")
    response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", _FreshStatic(directory=STATIC_DIR), name="static")
