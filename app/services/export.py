"""Excel and CSV exports for the current lead list."""

from __future__ import annotations

import csv
import io
from urllib.parse import quote_plus

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

HEADERS = [
    "Name",
    "Phone",
    "Phone (international)",
    "WhatsApp",
    "Email",
    "Website",
    "Facebook",
    "Instagram",
    "Address",
    "City",
    "Area",
    "Latitude",
    "Longitude",
    "Opening hours",
    "Rating",
    "Source",
    "Status",
    "Notes",
    "Follow-up",
    "Created",
    "Map",
]

STATUS_FILLS = {
    "New": "E2E8F0",
    "Contacted": "DBEAFE",
    "Interested": "FEF3C7",
    "Demo Scheduled": "EDE9FE",
    "Demo Done": "E0E7FF",
    "Customer": "D1FAE5",
    "Not Interested": "FFE4E6",
    "Wrong Number": "FFEDD5",
}

_HEADER_FILL = PatternFill("solid", fgColor="1E293B")
_HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
_LINK_FONT = Font(name="Calibri", color="9A6700", underline="single", size=11)
_BODY_FONT = Font(name="Calibri", size=11)
_THIN = Border(
    left=Side(style="thin", color="E2E8F0"),
    right=Side(style="thin", color="E2E8F0"),
    top=Side(style="thin", color="E2E8F0"),
    bottom=Side(style="thin", color="E2E8F0"),
)
_WRAP = Alignment(vertical="center", wrap_text=True)
_CENTER = Alignment(vertical="center")


# A coordinate-only Maps link opens a pin. Google then hides the business
# card, including the phone number. Search by shop name instead.
_PLACEHOLDER_NAMES = {"jewellery shop", "jewelry shop", "shop"}


def map_query(lead: dict) -> str:
    """Shop name and city. The street line is left out on purpose.

    OpenStreetMap streets are often in another script. Putting that text in
    the Google search opens a results page instead of the business card.
    The known pin centers the map; the saved place link opens the listing.
    """
    name = str(lead.get("name") or "").strip()
    if name.casefold() in _PLACEHOLDER_NAMES:
        name = ""
    city = str(lead.get("city") or "").strip()
    parts: list[str] = []
    if name:
        parts.append(name)
    if city and city.casefold() not in name.casefold():
        parts.append(city)
    return ", ".join(parts)


def map_link(lead: dict) -> str:
    stored = str(lead.get("listing_url") or "").strip()
    if stored.startswith("https://www.google.com/maps/") or stored.startswith("https://maps.app.goo.gl/"):
        return stored
    query = map_query(lead)
    name = str(lead.get("name") or "").strip()
    lat = lead.get("lat")
    lng = lead.get("lng")
    has_point = lat not in (None, "") and lng not in (None, "")
    # An unnamed pin has no shop title. Searching the city opens a list of
    # many shops. Open jewellery results centered on this one pin instead.
    if name.casefold() in _PLACEHOLDER_NAMES and has_point:
        return f"https://www.google.com/maps/search/jewellery/@{lat},{lng},19z"
    if query and has_point:
        # Center the camera on the known point, but search by name so Google
        # can show the business card (phone, hours) instead of a bare pin.
        return f"https://www.google.com/maps/search/{quote_plus(query)}/@{lat},{lng},18z"
    if query:
        return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query)
    if has_point:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    return ""


def _http(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("www."):
        return "https://" + value
    return ""


def _row_values(lead: dict) -> list[object]:
    rating = lead.get("rating")
    return [
        lead.get("name") or "",
        lead.get("phone") or "",
        lead.get("phone_normalized") or "",
        lead.get("whatsapp") or "",
        lead.get("email") or "",
        _http(str(lead.get("website") or "")),
        _http(str(lead.get("facebook") or "")),
        _http(str(lead.get("instagram") or "")),
        lead.get("address") or "",
        lead.get("city") or "",
        lead.get("area") or "",
        lead.get("lat") if lead.get("lat") is not None else "",
        lead.get("lng") if lead.get("lng") is not None else "",
        lead.get("opening_hours") or "",
        rating if rating is not None else "",
        lead.get("source") or "",
        lead.get("status") or "",
        lead.get("notes") or "",
        lead.get("follow_up_date") or "",
        (lead.get("created_at") or "")[:19].replace("T", " "),
        map_link(lead),
    ]


def _apply_link(cell, url: str) -> None:
    if not url:
        return
    cell.hyperlink = url
    cell.font = _LINK_FONT


def _style_sheet(ws: Worksheet, row_count: int) -> None:
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"
    last_row = max(row_count + 1, 1)
    last_col = get_column_letter(len(HEADERS))
    ws.auto_filter.ref = f"A1:{last_col}{last_row}"
    ws.auto_filter.ref = f"A1:{last_col}{max(last_row, 2)}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.tabColor = "D97706"
    for index, column in enumerate(ws.columns, start=1):
        length = 0
        for cell in column:
            if cell.value is not None:
                length = max(length, min(42, len(str(cell.value))))
        ws.column_dimensions[get_column_letter(index)].width = max(12, length + 2)


def build_workbook(leads: list[dict]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Leads"
    workbook.properties.title = "JewelLeads"
    workbook.properties.creator = "JewelLeads"
    sheet.append(HEADERS)
    link_columns = {6, 7, 8, 21}  # Website, Facebook, Instagram, Map (1-based)
    status_column = 17
    for row_index, lead in enumerate(leads, start=2):
        values = _row_values(lead)
        sheet.append(values)
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row_index, column)
            cell.font = _BODY_FONT
            cell.border = _THIN
            cell.alignment = _WRAP if column in {9, 18} else _CENTER
            if column in link_columns and value:
                _apply_link(cell, str(value))
        status = str(lead.get("status") or "")
        fill = STATUS_FILLS.get(status)
        if fill:
            sheet.cell(row_index, status_column).fill = PatternFill("solid", fgColor=fill)
        sheet.row_dimensions[row_index].height = 32
    _style_sheet(sheet, len(leads))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_csv(leads: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for lead in leads:
        writer.writerow(_row_values(lead))
    return buffer.getvalue().encode("utf-8-sig")
