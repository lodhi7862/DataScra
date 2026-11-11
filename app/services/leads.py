"""Create, update, list, and de-duplicate leads."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid

from app.db import IntegrityError, country_code, ensure_db, load_settings, session, using_postgres
from app.errors import AppError
from app.models import CLOSED_STATUSES, SOURCES, STATUSES, now_iso, today_iso
from app.services.dedupe import is_real_name, is_same_lead, merge_into_existing, numbers_of, record_changed
from app.services.importer import normalise_status, parse_follow_up
from app.services.phone import choose_primary, normalize_many, normalize_phone

LEAD_FIELDS = (
    "name",
    "phone",
    "phone_normalized",
    "whatsapp",
    "email",
    "website",
    "facebook",
    "instagram",
    "address",
    "city",
    "area",
    "lat",
    "lng",
    "rating",
    "source",
    "status",
    "notes",
    "follow_up_date",
    "osm_type",
    "osm_id",
    "opening_hours",
    "listing_url",
    "created_at",
    "updated_at",
)

SORT_COLUMNS = {
    "name": "name COLLATE NOCASE",
    "phone": "phone_normalized",
    "city": "city COLLATE NOCASE",
    "area": "area COLLATE NOCASE",
    "source": "source",
    "status": "status",
    "follow_up_date": "follow_up_date",
    "created_at": "created_at",
    "updated_at": "updated_at",
    "rating": "rating",
}

SEARCH_COLUMNS = (
    "name",
    "phone",
    "phone_normalized",
    "whatsapp",
    "email",
    "address",
    "city",
    "area",
    "notes",
    "facebook",
    "website",
)

LIMITS = {
    "name": 200,
    "phone": 300,
    "whatsapp": 32,
    "email": 200,
    "website": 500,
    "facebook": 500,
    "instagram": 500,
    "address": 500,
    "city": 120,
    "area": 120,
    "notes": 5000,
    "opening_hours": 300,
    "listing_url": 500,
    "osm_type": 20,
    "osm_id": 32,
}


def lead_dict(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    for key in ("lat", "lng", "rating"):
        if data.get(key) is None:
            continue
        data[key] = float(data[key])
    return data


def clean_listing_url(value: object) -> str:
    """Keep a Google place link. A name-search URL is not stored."""
    text = str(value or "").strip()
    if text.startswith("https://www.google.com/maps/place/") or text.startswith("https://maps.app.goo.gl/"):
        return text[: LIMITS["listing_url"]]
    return ""


def _clip(value: object, key: str) -> str:
    text = str(value or "").strip()
    limit = LIMITS.get(key)
    if limit:
        return text[:limit]
    return text


def _clean_osm(osm_type: object, osm_id: object) -> tuple[str, str]:
    kind = str(osm_type or "").strip().casefold()
    identifier = re.sub(r"\D", "", str(osm_id or ""))
    if kind not in {"node", "way", "relation"} or not identifier:
        return "", ""
    return kind, identifier


def _coord(value: object, low: float, high: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not low <= number <= high:
        return None
    return number


def apply_phones(record: dict, country: str, *, whatsapp_explicit: bool) -> None:
    parsed = normalize_many(record.get("phone") or "", country)
    primary = choose_primary(parsed)
    if parsed:
        record["phone"] = "; ".join(item.original.strip() for item in parsed)[: LIMITS["phone"]]
        record["phone_normalized"] = primary.normalized if primary else ""
    else:
        record["phone"] = _clip(record.get("phone"), "phone")
        record["phone_normalized"] = ""
    if whatsapp_explicit:
        result = normalize_phone(record.get("whatsapp") or "", country)
        record["whatsapp"] = result.normalized if result.is_mobile and result.normalized else ""
    else:
        mobile = next((item for item in parsed if item.is_mobile), None)
        record["whatsapp"] = mobile.normalized if mobile and mobile.normalized else ""


def prepare_record(data: dict, country: str, *, default_source: str, whatsapp_explicit: bool) -> dict:
    osm_type, osm_id = _clean_osm(data.get("osm_type"), data.get("osm_id"))
    source = str(data.get("source") or default_source)
    if source not in SOURCES:
        source = default_source if default_source in SOURCES else "Manual"
    status = str(data.get("status") or "New")
    if status not in STATUSES:
        status = "New"
    rating = data.get("rating")
    if rating is not None and rating != "":
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            rating = None
        if rating is not None and not 0 <= rating <= 5:
            rating = None
    else:
        rating = None
    record = {
        "name": _clip(data.get("name") or "Unnamed shop", "name") or "Unnamed shop",
        "phone": data.get("phone") or "",
        "phone_normalized": "",
        "whatsapp": data.get("whatsapp") or "",
        "email": _clip(data.get("email"), "email"),
        "website": _clip(data.get("website"), "website"),
        "facebook": _clip(data.get("facebook"), "facebook"),
        "instagram": _clip(data.get("instagram"), "instagram"),
        "address": _clip(data.get("address"), "address"),
        "city": _clip(data.get("city"), "city"),
        "area": _clip(data.get("area"), "area"),
        "lat": _coord(data.get("lat"), -90, 90),
        "lng": _coord(data.get("lng"), -180, 180),
        "rating": rating,
        "source": source,
        "status": status,
        "notes": _clip(data.get("notes"), "notes"),
        "follow_up_date": str(data.get("follow_up_date") or "")[:10],
        "osm_type": osm_type,
        "osm_id": osm_id,
        "opening_hours": _clip(data.get("opening_hours"), "opening_hours"),
        "listing_url": clean_listing_url(data.get("listing_url")),
    }
    apply_phones(record, country, whatsapp_explicit=whatsapp_explicit)
    return record


def _insert(conn: sqlite3.Connection, record: dict) -> int:
    stamp = now_iso()
    payload = dict(record)
    payload["created_at"] = stamp
    payload["updated_at"] = stamp
    columns = list(LEAD_FIELDS)
    values = [payload.get(column) for column in columns]
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO leads ({', '.join(columns)}) VALUES ({placeholders})"
    if using_postgres():
        sql += " RETURNING id"
    try:
        cursor = conn.execute(sql, values)
    except IntegrityError:
        # Another row already owns this OSM id; merge into that row instead.
        existing = conn.execute(
            "SELECT * FROM leads WHERE osm_type = ? AND osm_id = ?",
            (record.get("osm_type"), record.get("osm_id")),
        ).fetchone()
        if existing is None:
            raise
        return _update_merged(conn, lead_dict(existing), record)
    if using_postgres():
        return int(cursor.fetchone()["id"])
    return int(cursor.lastrowid)


def _update_merged(conn: sqlite3.Connection, existing: dict, incoming: dict) -> int:
    merged = merge_into_existing(existing, incoming)
    if not record_changed(existing, merged):
        return int(existing["id"])
    assignments = [
        "name",
        "phone",
        "phone_normalized",
        "whatsapp",
        "email",
        "website",
        "facebook",
        "instagram",
        "address",
        "city",
        "area",
        "lat",
        "lng",
        "rating",
        "notes",
        "osm_type",
        "osm_id",
        "opening_hours",
        "listing_url",
        "updated_at",
    ]
    merged["updated_at"] = now_iso()
    sets = ", ".join(f"{column} = ?" for column in assignments)
    conn.execute(
        f"UPDATE leads SET {sets} WHERE id = ?",
        [merged.get(column) for column in assignments] + [existing["id"]],
    )
    return int(existing["id"])


def find_existing(conn: sqlite3.Connection, record: dict, country: str) -> dict | None:
    osm_id = record.get("osm_id") or ""
    if osm_id:
        row = conn.execute(
            "SELECT * FROM leads WHERE osm_type = ? AND osm_id = ?",
            (record.get("osm_type") or "", osm_id),
        ).fetchone()
        if row is not None:
            return lead_dict(row)
    numbers = list(numbers_of(record, country))
    if numbers:
        marks = ", ".join("?" for _ in numbers)
        row = conn.execute(
            f"SELECT * FROM leads WHERE phone_normalized IN ({marks}) OR whatsapp IN ({marks}) LIMIT 1",
            [*numbers, *numbers],
        ).fetchone()
        if row is not None:
            return lead_dict(row)
    city = (record.get("city") or "").strip()
    if city and is_real_name(record.get("name")):
        rows = conn.execute(
            "SELECT * FROM leads WHERE city = ? COLLATE NOCASE",
            (city,),
        ).fetchall()
        for row in rows:
            candidate = lead_dict(row)
            if is_same_lead(candidate, record, country):
                return candidate
    return None


def save_record(conn: sqlite3.Connection, record: dict, country: str) -> tuple[str, int]:
    existing = find_existing(conn, record, country)
    if existing is None:
        return "created", _insert(conn, record)
    merged = merge_into_existing(existing, record)
    if not record_changed(existing, merged):
        return "unchanged", int(existing["id"])
    return "merged", _update_merged(conn, existing, record)


def save_shops(shops: list[dict]) -> dict:
    ensure_db()
    country = country_code()
    counts = {"created": 0, "merged": 0, "unchanged": 0}
    items: list[dict] = []
    with session() as conn:
        for index, shop in enumerate(shops):
            record = prepare_record(
                shop,
                country,
                default_source="OpenStreetMap",
                whatsapp_explicit=bool(str(shop.get("whatsapp") or "").strip()),
            )
            record["source"] = "OpenStreetMap"
            record["status"] = "New"
            action, lead_id = save_record(conn, record, country)
            counts[action] += 1
            saved = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
            items.append(
                {
                    "index": index,
                    "id": lead_id,
                    "action": action,
                    "status": saved["status"] if saved else "New",
                }
            )
        conn.commit()
    return {**counts, "items": items}


def create_lead(data: dict) -> dict:
    ensure_db()
    country = country_code()
    explicit = "whatsapp" in data and bool(str(data.get("whatsapp") or "").strip())
    record = prepare_record(data, country, default_source="Manual", whatsapp_explicit=explicit)
    with session() as conn:
        action, lead_id = save_record(conn, record, country)
        conn.commit()
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        lead = lead_dict(row)
    return {"action": action, "lead": lead}


def update_lead(lead_id: int, changes: dict) -> dict:
    ensure_db()
    country = country_code()
    with session() as conn:
        current = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if current is None:
            raise AppError("That lead was not found.", status_code=404)
        updated = lead_dict(current)
        phone_changed = "phone" in changes
        whatsapp_changed = "whatsapp" in changes
        for key, value in changes.items():
            if key in updated:
                updated[key] = value
        if phone_changed or whatsapp_changed:
            apply_phones(updated, country, whatsapp_explicit=whatsapp_changed)
        if "lat" in changes:
            updated["lat"] = _coord(changes.get("lat"), -90, 90)
        if "lng" in changes:
            updated["lng"] = _coord(changes.get("lng"), -180, 180)
        if "rating" in changes:
            rating = changes.get("rating")
            if rating is None or rating == "":
                updated["rating"] = None
            else:
                updated["rating"] = float(rating)
        for key in (
            "name",
            "email",
            "website",
            "facebook",
            "instagram",
            "address",
            "city",
            "area",
            "notes",
            "opening_hours",
        ):
            if key in changes:
                updated[key] = _clip(updated.get(key), key)
        if "listing_url" in changes:
            cleaned = clean_listing_url(changes.get("listing_url"))
            if cleaned or not str(changes.get("listing_url") or "").strip():
                updated["listing_url"] = cleaned
            else:
                updated["listing_url"] = clean_listing_url(current["listing_url"])
        if not str(updated.get("name") or "").strip():
            raise AppError("A lead needs a name.", status_code=400)
        stamp = now_iso()
        columns = [
            "name",
            "phone",
            "phone_normalized",
            "whatsapp",
            "email",
            "website",
            "facebook",
            "instagram",
            "address",
            "city",
            "area",
            "lat",
            "lng",
            "rating",
            "source",
            "status",
            "notes",
            "follow_up_date",
            "opening_hours",
            "listing_url",
            "updated_at",
        ]
        updated["updated_at"] = stamp
        sets = ", ".join(f"{column} = ?" for column in columns)
        conn.execute(
            f"UPDATE leads SET {sets} WHERE id = ?",
            [updated.get(column) for column in columns] + [lead_id],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return lead_dict(row)


def get_lead(lead_id: int) -> dict:
    ensure_db()
    with session() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            found = None
        else:
            found = lead_dict(row)
    if found is None:
        raise AppError("That lead was not found.", status_code=404)
    return found


def delete_leads(ids: list[int]) -> int:
    ensure_db()
    unique = [int(lead_id) for lead_id in dict.fromkeys(ids)]
    if not unique:
        return 0
    marks = ", ".join("?" for _ in unique)
    with session() as conn:
        cursor = conn.execute(f"DELETE FROM leads WHERE id IN ({marks})", unique)
        conn.commit()
        return int(cursor.rowcount)


def set_status(ids: list[int], status: str) -> int:
    if status not in STATUSES:
        raise AppError("Choose a valid status.", status_code=400)
    ensure_db()
    unique = [int(lead_id) for lead_id in dict.fromkeys(ids)]
    marks = ", ".join("?" for _ in unique)
    with session() as conn:
        cursor = conn.execute(
            f"UPDATE leads SET status = ?, updated_at = ? WHERE id IN ({marks})",
            [status, now_iso(), *unique],
        )
        conn.commit()
        return int(cursor.rowcount)


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _filters(
    *,
    q: str = "",
    city: str = "",
    status: str = "",
    source: str = "",
    has_phone: str = "",
    has_whatsapp: str = "",
    ids: list[int] | None = None,
) -> tuple[str, list]:
    clauses = ["1 = 1"]
    params: list = []
    if ids:
        marks = ", ".join("?" for _ in ids)
        clauses.append(f"id IN ({marks})")
        params.extend(ids)
        return " AND ".join(clauses), params
    if q.strip():
        needle = _like(q.strip())
        parts = [f"{column} LIKE ? ESCAPE '\\'" for column in SEARCH_COLUMNS]
        clauses.append("(" + " OR ".join(parts) + ")")
        params.extend([needle] * len(SEARCH_COLUMNS))
    if city.strip():
        clauses.append("city = ? COLLATE NOCASE")
        params.append(city.strip())
    if status.strip():
        clauses.append("status = ?")
        params.append(status.strip())
    if source.strip():
        clauses.append("source = ?")
        params.append(source.strip())
    if has_phone == "yes":
        clauses.append("(phone_normalized != '' OR phone != '')")
    elif has_phone == "no":
        clauses.append("phone_normalized = '' AND phone = ''")
    if has_whatsapp == "yes":
        clauses.append("whatsapp != ''")
    elif has_whatsapp == "no":
        clauses.append("whatsapp = ''")
    return " AND ".join(clauses), params


def list_leads(
    *,
    q: str = "",
    city: str = "",
    status: str = "",
    source: str = "",
    has_phone: str = "",
    has_whatsapp: str = "",
    sort: str = "created_at",
    direction: str = "desc",
    page: int = 1,
    page_size: int = 25,
    ids: list[int] | None = None,
    paginate: bool = True,
) -> dict:
    ensure_db()
    page = max(1, page)
    page_size = min(100, max(10, page_size))
    order_column = SORT_COLUMNS.get(sort, SORT_COLUMNS["created_at"])
    order_dir = "ASC" if direction.lower() == "asc" else "DESC"
    where, params = _filters(
        q=q,
        city=city,
        status=status,
        source=source,
        has_phone=has_phone,
        has_whatsapp=has_whatsapp,
        ids=ids,
    )
    with session() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM leads WHERE {where}", params).fetchone()["n"])
        sql = f"SELECT * FROM leads WHERE {where} ORDER BY {order_column} {order_dir}, id DESC"
        query_params = list(params)
        if paginate:
            sql += " LIMIT ? OFFSET ?"
            query_params.extend([page_size, (page - 1) * page_size])
        else:
            sql += " LIMIT 10000"
        rows = [lead_dict(row) for row in conn.execute(sql, query_params).fetchall()]
        cities = [
            row["city"]
            for row in conn.execute(
                "SELECT DISTINCT city FROM leads WHERE trim(city) != '' ORDER BY city COLLATE NOCASE"
            )
        ]
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "cities": cities,
    }


def dashboard() -> dict:
    ensure_db()
    today = today_iso()
    with session() as conn:
        total = int(conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"])
        grouped = [dict(row) for row in conn.execute("SELECT status, COUNT(*) AS n FROM leads GROUP BY status").fetchall()]
        closed_values = sorted(CLOSED_STATUSES)
        closed = ", ".join("?" for _ in closed_values)
        due_today = conn.execute(
            f"SELECT COUNT(*) AS n FROM leads WHERE follow_up_date = ? AND status NOT IN ({closed})",
            [today, *closed_values],
        ).fetchone()["n"]
        overdue = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM leads
            WHERE follow_up_date != '' AND follow_up_date < ? AND status NOT IN ({closed})
            """,
            [today, *closed_values],
        ).fetchone()["n"]
        followups = conn.execute(
            f"""
            SELECT * FROM leads
            WHERE follow_up_date != ''
              AND follow_up_date <= ?
              AND status NOT IN ({closed})
            ORDER BY follow_up_date ASC, name COLLATE NOCASE
            LIMIT 30
            """,
            [today, *closed_values],
        ).fetchall()
        followups = [lead_dict(row) for row in followups]
    by_status = {status: 0 for status in STATUSES}
    for row in grouped:
        by_status[row["status"]] = int(row["n"])
    items = []
    for lead in followups:
        lead["overdue"] = lead["follow_up_date"] < today
        items.append(lead)
    return {
        "total": total,
        "by_status": by_status,
        "new": by_status.get("New", 0),
        "contacted": by_status.get("Contacted", 0),
        "interested": by_status.get("Interested", 0),
        "customers": by_status.get("Customer", 0),
        "followups_today": int(due_today),
        "followups_overdue": int(overdue),
        "followups": items,
        "today": today,
    }


def annotate_saved(shops: list[dict]) -> None:
    """Mark search hits that are already in the CRM. Does not write."""
    ensure_db()
    country = country_code()
    with session() as conn:
        rows = [lead_dict(row) for row in conn.execute("SELECT * FROM leads")]
    by_osm: dict[str, dict] = {}
    by_phone: dict[str, dict] = {}
    for row in rows:
        if row.get("osm_id"):
            by_osm[f"{row.get('osm_type')}/{row.get('osm_id')}"] = row
        for number in numbers_of(row, country):
            by_phone.setdefault(number, row)
    for shop in shops:
        match: dict | None = None
        if shop.get("osm_id"):
            match = by_osm.get(f"{shop.get('osm_type')}/{shop.get('osm_id')}")
        if match is None:
            for number in numbers_of(shop, country):
                if number in by_phone:
                    match = by_phone[number]
                    break
        if match is None and is_real_name(shop.get("name")):
            for row in rows:
                if is_same_lead(row, shop, country):
                    match = row
                    break
        shop["lead_id"] = match["id"] if match else None
        shop["lead_status"] = match["status"] if match else None
        if match and match.get("listing_url") and not shop.get("listing_url"):
            shop["listing_url"] = match["listing_url"]


def store_import(filename: str, rows: list[dict]) -> str:
    ensure_db()
    token = uuid.uuid4().hex
    with session() as conn:
        conn.execute(
            "INSERT INTO import_batches(token, filename, rows_json, created_at) VALUES(?, ?, ?, ?)",
            (token, filename[:200], json.dumps(rows), now_iso()),
        )
        conn.commit()
    return token


def load_import(token: str) -> tuple[str, list[dict]]:
    ensure_db()
    with session() as conn:
        row = conn.execute(
            "SELECT filename, rows_json FROM import_batches WHERE token = ?",
            (token,),
        ).fetchone()
        loaded = None if row is None else (row["filename"], row["rows_json"])
    if loaded is None:
        raise AppError("That import expired. Upload the file again.", status_code=404)
    return loaded[0], json.loads(loaded[1])


def drop_import(token: str) -> None:
    with session() as conn:
        conn.execute("DELETE FROM import_batches WHERE token = ?", (token,))
        conn.commit()


def import_mapped_rows(token: str, mapping: dict[str, str], default_source: str) -> dict:
    _filename, rows = load_import(token)
    country = country_code()
    counts = {"created": 0, "merged": 0, "unchanged": 0, "skipped": 0}
    usable = {field: header for field, header in mapping.items() if header}
    if "name" not in usable and "phone" not in usable and "whatsapp" not in usable:
        raise AppError("Map at least a name column or a phone column.", status_code=400)
    with session() as conn:
        for row in rows:
            raw = {field: row.get(header, "") for field, header in usable.items()}
            if not str(raw.get("name") or "").strip() and not str(raw.get("phone") or "").strip() and not str(raw.get("whatsapp") or "").strip():
                counts["skipped"] += 1
                continue
            follow = parse_follow_up(str(raw.get("follow_up_date") or ""))
            status = normalise_status(str(raw.get("status") or "")) or "New"
            source = str(raw.get("source") or "").strip()
            if source not in SOURCES:
                source = default_source if default_source in SOURCES else "Import"
            raw["follow_up_date"] = follow
            raw["status"] = status
            raw["source"] = source
            explicit = bool(str(raw.get("whatsapp") or "").strip())
            record = prepare_record(raw, country, default_source=source, whatsapp_explicit=explicit)
            record["status"] = status
            record["source"] = source
            record["follow_up_date"] = follow
            action, _lead_id = save_record(conn, record, country)
            counts[action] += 1
        conn.execute("DELETE FROM import_batches WHERE token = ?", (token,))
        conn.commit()
    return counts


def settings_public() -> dict:
    values = load_settings()
    return {
        "country_code": values.get("country_code") or "92",
        "my_name": values.get("my_name") or "",
        "whatsapp_template": values.get("whatsapp_template") or "",
        "statuses": STATUSES,
        "sources": SOURCES,
    }
