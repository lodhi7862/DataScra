"""API behaviour that does not need the public map servers."""

import os
import tempfile
from pathlib import Path

import pytest

_dir = tempfile.mkdtemp(prefix="jewelleads-")
os.environ["JEWELLEADS_DB"] = str(Path(_dir) / "test.db")
os.environ.pop("DATABASE_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db, save_settings, session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import DEFAULT_SETTINGS  # noqa: E402


@pytest.fixture()
def client():
    init_db()
    with session() as conn:
        conn.execute("DELETE FROM leads")
        conn.execute("DELETE FROM import_batches")
        conn.execute("DELETE FROM search_cache")
        conn.execute("DELETE FROM geocode_cache")
    save_settings(DEFAULT_SETTINGS)
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["app"] == "JewelLeads"
    root = client.get("/health")
    assert root.status_code == 200
    assert root.json()["status"] == "ok"


def test_password_protects_the_app_and_login_keeps_a_session(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret-pass")
    assert client.get("/health").status_code == 200
    blocked = client.get("/api/leads")
    assert blocked.status_code == 401
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"
    wrong = client.post("/login", data={"password": "nope"})
    assert wrong.status_code == 401
    signed_in = client.post("/login", data={"password": "secret-pass"}, follow_redirects=False)
    assert signed_in.status_code == 303
    leads = client.get("/api/leads")
    assert leads.status_code == 200
    created = client.post("/api/leads", json={"name": "Login Check", "city": "Lahore"})
    assert created.status_code == 200
    assert created.json()["lead"]["name"] == "Login Check"
    logged_out = client.post("/logout", follow_redirects=False)
    assert logged_out.status_code == 303
    assert client.get("/api/leads").status_code == 401


def test_create_normalises_phone_and_merges_duplicate(client):
    created = client.post("/api/leads", json={"name": "Ali Jewellers", "phone": "03001234567", "city": "Rawalpindi"})
    assert created.status_code == 200
    body = created.json()
    assert body["action"] == "created"
    assert body["lead"]["phone_normalized"] == "+923001234567"
    assert body["lead"]["whatsapp"] == "+923001234567"
    assert body["lead"]["source"] == "Manual"
    lead_id = body["lead"]["id"]

    patched = client.patch(f"/api/leads/{lead_id}", json={"status": "Contacted", "notes": "Called once"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "Contacted"

    merged = client.post(
        "/api/leads",
        json={"name": "Ali Jewellers Branch", "phone": "0300-1234567", "email": "ali@example.com", "notes": "ignore"},
    )
    assert merged.status_code == 200
    assert merged.json()["action"] == "merged"
    lead = merged.json()["lead"]
    assert lead["id"] == lead_id
    assert lead["email"] == "ali@example.com"
    assert lead["notes"] == "Called once"
    assert lead["status"] == "Contacted"

    listed = client.get("/api/leads")
    assert listed.json()["total"] == 1


def test_dashboard_followups_and_export(client):
    created = client.post(
        "/api/leads",
        json={"name": "Noor Jewellers", "phone": "051-2345678", "city": "Rawalpindi", "follow_up_date": "2020-01-01"},
    )
    assert created.json()["lead"]["whatsapp"] == ""
    assert created.json()["lead"]["phone_normalized"] == "+92512345678"
    dash = client.get("/api/dashboard").json()
    assert dash["total"] == 1
    assert dash["new"] == 1
    assert dash["followups_overdue"] == 1
    workbook = client.get("/api/export/xlsx")
    assert workbook.status_code == 200
    assert workbook.content[:2] == b"PK"
    csv_file = client.get("/api/export/csv")
    assert "Noor Jewellers" in csv_file.content.decode("utf-8-sig")


def test_parse_and_import(client):
    parsed = client.post(
        "/api/parse-text",
        json={"text": "Al-Rahim Jewellers\nAddress: Saddar Road\n0300-1234567\nhttps://facebook.com/alrahim"},
    )
    assert parsed.status_code == 200
    assert parsed.json()["whatsapp"] == "+923001234567"
    assert parsed.json()["source"] == "Facebook"

    preview = client.post(
        "/api/import/preview",
        files={"file": ("shops.csv", b"Shop Name,Mobile,City\nNoor Gold,03111234567,Rawalpindi\n", "text/csv")},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["suggested"]["name"] == "Shop Name"
    assert body["suggested"]["phone"] == "Mobile"
    committed = client.post(
        "/api/import/commit",
        json={"token": body["token"], "mapping": body["suggested"], "source": "Import"},
    )
    assert committed.status_code == 200
    assert committed.json()["created"] == 1
    rows = client.get("/api/leads?source=Import").json()
    assert rows["total"] == 1
    assert rows["items"][0]["whatsapp"] == "+923111234567"


def test_settings_validation(client):
    bad = client.put("/api/settings", json={"country_code": "pk", "my_name": "Ada", "whatsapp_template": "Hi"})
    assert bad.status_code == 422
    good = client.put(
        "/api/settings",
        json={"country_code": "+92", "my_name": "Ada", "whatsapp_template": "Hello {shop_name}, I am {my_name}."},
    )
    assert good.status_code == 200
    assert good.json()["country_code"] == "92"
    assert good.json()["my_name"] == "Ada"


def test_search_requires_a_real_city_name(client):
    response = client.post("/api/search", json={"city": "a"})
    assert response.status_code == 422


def test_place_link_is_kept_and_a_search_link_is_not(client):
    place = (
        "https://www.google.com/maps/place/SAJ+Designer+Jewellery/@33.586696,73.0875286,17z/"
        "data=!4m2!3m1!1s0x38df9352b261b27d:0xe5ec65511482eaae"
    )
    created = client.post(
        "/api/leads",
        json={"name": "saj jewllers", "city": "Rawalpindi", "lat": 33.586666, "lng": 73.087475, "listing_url": place},
    )
    assert created.status_code == 200
    assert created.json()["lead"]["listing_url"] == place
    lead_id = created.json()["lead"]["id"]
    searched = client.patch(
        f"/api/leads/{lead_id}",
        json={"listing_url": "https://www.google.com/maps/search/saj+jewllers/@33.58,73.08,18z"},
    )
    assert searched.status_code == 200
    assert searched.json()["listing_url"] == place
