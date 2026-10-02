"""Detection endpoints. Currently: CSV upload that registers a pending batch."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiException
from app.db.database import get_db
from app.schemas.batch import UploadBatchResponse
from app.schemas.common import ErrorResponse
from app.services.upload_service import UploadConfig, register_upload

router = APIRouter(prefix="/detection")


def get_upload_config() -> UploadConfig:
    """Dependency so tests can point uploads at a temporary directory and limit."""
    return UploadConfig(directory=Path(settings.upload_dir), max_bytes=settings.max_upload_mb * 1024 * 1024)


@router.post(
    "/upload",
    response_model=UploadBatchResponse,
    status_code=201,
    summary="Upload a network-flow CSV and register a pending batch",
    description=(
        "Validates the CSV's structure, stores it, and creates a DetectionBatch with status "
        "`pending`. The traffic is not analyzed by this endpoint."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Missing, empty or malformed CSV"},
        413: {"model": ErrorResponse, "description": "File exceeds the upload size limit"},
        415: {"model": ErrorResponse, "description": "Not a CSV file"},
        500: {"model": ErrorResponse, "description": "The upload could not be registered"},
    },
)
def upload_detection_csv(
    file: UploadFile | None = File(default=None, description="The CSV file (multipart field `file`)."),
    db: Session = Depends(get_db),
    config: UploadConfig = Depends(get_upload_config),
) -> UploadBatchResponse:
    if file is None:
        raise ApiException(400, "missing_file", "No file was uploaded. Send a CSV in the `file` form field.")

    batch = register_upload(
        db=db,
        config=config,
        source=file.file,
        filename=file.filename,
        content_type=file.content_type,
    )
    return UploadBatchResponse(
        batch_id=batch.id,
        filename=batch.filename,
        status=batch.status,
        total_records=batch.total_records,
        processed_records=batch.processed_records,
        failed_records=batch.failed_records,
        created_at=batch.created_at,
    )
