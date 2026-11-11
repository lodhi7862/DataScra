"""One shared password, stored only in the APP_PASSWORD environment variable."""

from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

router = APIRouter()

_PUBLIC_PATHS = {"/login", "/logout", "/health", "/api/health"}


def app_password() -> str:
    return os.environ.get("APP_PASSWORD", "").strip()


def secret_key() -> str:
    return os.environ.get("SECRET_KEY", "").strip() or "jewelleads-local-dev-secret"


def https_only() -> bool:
    flag = os.environ.get("COOKIE_SECURE", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    if flag in {"0", "false", "no"}:
        return False
    return os.environ.get("RENDER", "").strip().lower() == "true"


def passwords_match(given: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(given.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def signed_in(request: Request) -> bool:
    if not app_password():
        return True
    return request.session.get("user") == "owner"


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not app_password():
            return await call_next(request)
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)
        if request.session.get("user") == "owner":
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": "Sign in required."})
        return RedirectResponse("/login", status_code=303)


def _login_page(error: str = "") -> HTMLResponse:
    message = f"<p class='error'>{error}</p>" if error else ""
    status = 401 if error else 200
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · JewelLeads</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
      font-family: Inter, ui-sans-serif, system-ui, sans-serif; background: #faf7f2; color: #1c1917; }}
    form {{ width: min(100% - 2rem, 22rem); background: #fff; border: 1px solid #e7e5e4;
      border-radius: 16px; padding: 1.5rem; box-shadow: 0 12px 40px rgba(28, 25, 23, .06); }}
    h1 {{ margin: 0 0 .25rem; font-size: 1.35rem; }}
    p {{ margin: 0 0 1rem; color: #57534e; }}
    label {{ display: block; font-size: .9rem; font-weight: 600; margin-bottom: .35rem; }}
    input {{ width: 100%; box-sizing: border-box; padding: .7rem .8rem; border-radius: 10px;
      border: 1px solid #d6d3d1; font: inherit; }}
    button {{ margin-top: 1rem; width: 100%; padding: .7rem .8rem; border: 0; border-radius: 10px;
      background: #b45309; color: #fff; font: inherit; font-weight: 600; cursor: pointer; }}
    .error {{ color: #b91c1c; font-weight: 600; }}
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>JewelLeads</h1>
    <p>Sign in to open your leads.</p>
    {message}
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>"""
    return HTMLResponse(html, status_code=status)


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_form(request: Request) -> HTMLResponse | RedirectResponse:
    if app_password() and request.session.get("user") == "owner":
        return RedirectResponse("/", status_code=303)
    return _login_page()


@router.post("/login", response_model=None)
async def login_submit(request: Request) -> HTMLResponse | RedirectResponse:
    expected = app_password()
    form = await request.form()
    given = str(form.get("password") or "")
    if not expected or not passwords_match(given, expected):
        return _login_page("That password is not correct.")
    request.session["user"] = "owner"
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login" if app_password() else "/", status_code=303)
