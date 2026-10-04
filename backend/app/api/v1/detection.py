"""Detection endpoints: CSV upload, batch listing, and single-batch retrieval."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiException
from app.db.database import get_db
from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus
from app.schemas.batch import (
    BatchProcessingResponse,
    DetectionBatchListResponse,
    DetectionBatchSummary,
    UploadBatchResponse,
)
from app.schemas.common import ErrorResponse
from app.schemas.detection import DetectionResultListResponse, DetectionResultResponse
from app.services.batch_processor import start_processing
from app.services.batch_service import get_batch, list_batches
from app.services.detection_result_service import get_detection_result, list_detection_results_for_batch
from app.services.upload_service import UploadConfig, register_upload

router = APIRouter(prefix="/detection")


def _summarize(batch: DetectionBatch) -> DetectionBatchSummary:
    return DetectionBatchSummary(
        batch_id=batch.id,
        filename=batch.filename,
        status=batch.status,
        total_records=batch.total_records,
        processed_records=batch.processed_records,
        failed_records=batch.failed_records,
        created_at=batch.created_at,
        completed_at=batch.completed_at,
    )


def _to_detection_result_response(result: DetectionResult) -> DetectionResultResponse:
    return DetectionResultResponse(
        id=result.id,
        batch_id=result.batch_id,
        model_id=result.model_id,
        model_name=result.model_name,
        model_version=result.model_version,
        row_number=result.row_number,
        predicted_label=result.predicted_label,
        prediction_name=result.prediction_name,
        attack_probability=result.attack_probability,
        created_at=result.created_at,
    )


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


MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


@router.get(
    "/batches",
    response_model=DetectionBatchListResponse,
    summary="List persisted detection batches, newest first",
    description=(
        "Returns one page of registered batches. An empty collection is a normal `200` with "
        "`items: []`. Batches are listed as stored: a freshly uploaded batch is `pending`."
    ),
    responses={
        422: {"model": ErrorResponse, "description": "Invalid page or page_size"},
        500: {"model": ErrorResponse, "description": "Batches could not be loaded"},
    },
)
def get_detection_batches(
    page: int = Query(default=1, ge=1, le=1_000_000, description="1-based page number."),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Batches per page."),
    db: Session = Depends(get_db),
) -> DetectionBatchListResponse:
    result = list_batches(db, page=page, page_size=page_size)
    return DetectionBatchListResponse(
        items=[_summarize(batch) for batch in result.items],
        page=page,
        page_size=page_size,
        total_items=result.total_items,
        total_pages=result.total_pages,
    )


@router.get(
    "/batches/{batch_id}",
    response_model=DetectionBatchSummary,
    summary="Retrieve one persisted detection batch by id",
    description=(
        "Returns the batch exactly as stored. A freshly registered batch is `pending`; no "
        "analysis has run on its traffic."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "No batch exists with that ID"},
        422: {"model": ErrorResponse, "description": "batch_id is not a valid UUID"},
        500: {"model": ErrorResponse, "description": "The batch could not be loaded"},
    },
)
def get_detection_batch(
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> DetectionBatchSummary:
    return _summarize(get_batch(db, batch_id))


_PROCESSING_MESSAGES = {
    ProcessingStatus.PROCESSING: "Batch processing started.",
    ProcessingStatus.FAILED: "Batch processing could not be started.",
}


@router.post(
    "/batches/{batch_id}/process",
    response_model=BatchProcessingResponse,
    summary="Start processing a pending detection batch",
    description=(
        "Claims a pending batch and transitions it to `processing`: CSV ingestion, feature "
        "persistence, model inference and DetectionResult persistence all run as part of this "
        "call for a recognized dataset schema with a compatible trained model (see the "
        "`GET /batches/{batch_id}/results` endpoint for the resulting predictions). The batch "
        "never reaches `completed` here, and processed/failed record counts are left at zero — "
        "those remain detection-outcome counters this pipeline does not populate yet."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "No batch exists with that ID"},
        409: {"model": ErrorResponse, "description": "The batch is not pending and cannot be started"},
        422: {"model": ErrorResponse, "description": "batch_id is not a valid UUID"},
        500: {"model": ErrorResponse, "description": "Processing could not be started"},
    },
)
def start_detection_batch_processing(
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
    config: UploadConfig = Depends(get_upload_config),
) -> BatchProcessingResponse:
    batch = start_processing(db, batch_id, config.directory)
    return BatchProcessingResponse(
        batch_id=batch.id,
        status=batch.status,
        message=_PROCESSING_MESSAGES[batch.status],
    )


@router.get(
    "/batches/{batch_id}/results",
    response_model=DetectionResultListResponse,
    summary="List persisted detection results for one batch, ordered by row_number",
    description=(
        "Returns one page of this batch's own DetectionResult rows — each a prediction "
        "already produced by model inference and persisted during processing (see "
        "`POST /batches/{batch_id}/process`). This endpoint performs no analysis itself: it "
        "only reads rows that already exist. An empty collection is a normal `200` with "
        "`items: []` (a batch that has not been processed yet, or whose schema was not "
        "recognized, simply has none)."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "No batch exists with that ID"},
        422: {"model": ErrorResponse, "description": "Invalid batch_id, page or page_size"},
        500: {"model": ErrorResponse, "description": "Detection results could not be loaded"},
    },
)
def get_batch_detection_results(
    batch_id: uuid.UUID,
    page: int = Query(default=1, ge=1, le=1_000_000, description="1-based page number."),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Results per page."),
    db: Session = Depends(get_db),
) -> DetectionResultListResponse:
    get_batch(db, batch_id)  # 404 if the batch itself does not exist
    result = list_detection_results_for_batch(db, batch_id, page=page, page_size=page_size)
    return DetectionResultListResponse(
        items=[_to_detection_result_response(row) for row in result.items],
        batch_id=batch_id,
        page=page,
        page_size=page_size,
        total_items=result.total_items,
        total_pages=result.total_pages,
    )


@router.get(
    "/results/{result_id}",
    response_model=DetectionResultResponse,
    summary="Retrieve one persisted detection result by id",
    description="Returns the detection result exactly as stored, including its originating model's identity.",
    responses={
        404: {"model": ErrorResponse, "description": "No detection result exists with that ID"},
        422: {"model": ErrorResponse, "description": "result_id is not a valid UUID"},
        500: {"model": ErrorResponse, "description": "The detection result could not be loaded"},
    },
)
def get_detection_result_by_id(
    result_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> DetectionResultResponse:
    return _to_detection_result_response(get_detection_result(db, result_id))
