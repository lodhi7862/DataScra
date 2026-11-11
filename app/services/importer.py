"""Read CSV / Excel files the user already has. Nothing is fetched remotely."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from openpyxl import load_workbook

from app.errors import AppError
from app.models import STATUSES

MAX_ROWS = 5000
MAX_BYTES = 5_000_000

SYNONYMS: dict[str, set[str]] = {
    "name": {"name", "shop", "shop name", "business", "business name", "store", "store name", "title"},
    "phone": {"phone", "phone number", "mobile", "mobile number", "tel", "telephone", "contact", "contact number"},
    "whatsapp": {"whatsapp", "whatsapp number", "wa", "wa number"},
    "email": {"email", "e-mail", "email address"},
    "website": {"website", "web", "url", "site"},
    "facebook": {"facebook", "fb", "facebook url", "facebook page"},
    "instagram": {"instagram", "ig", "instagram url"},
    "address": {"address", "full address", "location"},
    "city": {"city", "town"},
    "area": {"area", "suburb", "neighbourhood", "neighborhood", "locality", "sector"},
    "notes": {"notes", "note", "comments", "comment", "remarks"},
    "status": {"status"},
    "source": {"source"},
    "follow_up_date": {"follow up", "follow-up", "follow_up", "follow up date", "follow-up date", "next follow up"},
    "rating": {"rating", "stars"},
}


def _decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _cell_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _unique_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    unique: list[str] = []
    for header in headers:
        base = header.strip() or "Column"
        count = seen.get(base, 0) + 1
        seen[base] = count
        unique.append(base if count == 1 else f"{base} ({count})")
    return unique


def _rows_from_dicts(headers: list[str], raw_rows: list[list[str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in raw_rows:
        item = {headers[index]: raw[index] if index < len(raw) else "" for index in range(len(headers))}
        if any(value.strip() for value in item.values()):
            rows.append(item)
        if len(rows) > MAX_ROWS:
            raise AppError(f"Import files are limited to {MAX_ROWS} rows.", status_code=400)
    return rows


def parse_upload(filename: str, content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    if len(content) > MAX_BYTES:
        raise AppError("That file is larger than 5 MB.", status_code=400)
    name = (filename or "").casefold()
    if name.endswith(".csv") or name.endswith(".txt"):
        text = _decode_csv(content)
        reader = csv.reader(io.StringIO(text))
        try:
            header_row = next(reader)
        except StopIteration as exc:
            raise AppError("That file is empty.", status_code=400) from exc
        headers = _unique_headers([_cell_str(cell) for cell in header_row])
        body = [[_cell_str(cell) for cell in row] for row in reader]
        return headers, _rows_from_dicts(headers, body)
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise AppError("That Excel file could not be read. Save it as .xlsx and try again.", status_code=400) from exc
        sheet = workbook.active
        iterator = sheet.iter_rows(values_only=True)
        try:
            header_row = next(iterator)
        except StopIteration as exc:
            raise AppError("That spreadsheet is empty.", status_code=400) from exc
        headers = _unique_headers([_cell_str(cell) for cell in header_row])
        body = [[_cell_str(cell) for cell in row] for row in iterator]
        workbook.close()
        return headers, _rows_from_dicts(headers, body)
    raise AppError("Upload a .csv or .xlsx file.", status_code=400)


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    folded = {header: header.strip().casefold() for header in headers}
    for field, names in SYNONYMS.items():
        for header, label in folded.items():
            if header in used:
                continue
            if label in names:
                mapping[field] = header
                used.add(header)
                break
    return mapping


def parse_follow_up(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if not match:
        return ""
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def normalise_status(value: str) -> str:
    text = (value or "").strip().casefold()
    if not text:
        return ""
    for status in STATUSES:
        if status.casefold() == text:
            return status
    return ""
