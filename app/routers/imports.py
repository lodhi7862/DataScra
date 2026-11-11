"""CSV and Excel import. The file never leaves this computer."""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from app.errors import AppError
from app.schemas import ImportCommit
from app.services.importer import parse_upload, suggest_mapping
from app.services.leads import import_mapped_rows, store_import

router = APIRouter(tags=["import"])


@router.post("/import/preview")
async def preview_import(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    if not content:
        raise AppError("That file is empty.", status_code=400)
    headers, rows = parse_upload(file.filename or "upload.csv", content)
    if not rows:
        raise AppError("No data rows were found under the header.", status_code=400)
    token = store_import(file.filename or "upload", rows)
    return {
        "token": token,
        "filename": file.filename or "upload",
        "headers": headers,
        "suggested": suggest_mapping(headers),
        "sample": rows[:8],
        "row_count": len(rows),
    }


@router.post("/import/commit")
def commit_import(body: ImportCommit) -> dict:
    return import_mapped_rows(body.token, body.mapping, body.source)
