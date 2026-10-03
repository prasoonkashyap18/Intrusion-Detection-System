"""API schemas for detection batches.

Independent of app.models.detection_batch.DetectionBatch (the ORM model)
by design — see "Schema Separation" in backend/README.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, field_validator

from app.schemas.enums import ProcessingStatus


class UploadBatchResponse(BaseModel):
    """Result of registering an uploaded CSV. The traffic has NOT been analyzed:
    a new batch is always `pending` with zero processed and failed records."""

    batch_id: uuid.UUID
    filename: str
    status: ProcessingStatus
    total_records: int
    processed_records: int
    failed_records: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops timezone info on read. Every stored timestamp is UTC, so
        # restore it; otherwise clients would parse the value as local time.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class DetectionBatchSummary(UploadBatchResponse):
    """A persisted batch as listed by the API. Adds the completion time, which
    stays null until a future processing step finishes the batch."""

    completed_at: datetime | None = None

    @field_validator("completed_at")
    @classmethod
    def _assume_utc_completed(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class DetectionBatchListResponse(BaseModel):
    """One page of batches, newest first. An empty collection is a normal
    200 response with `items: []` and `total_pages: 0`."""

    items: list[DetectionBatchSummary]
    page: int
    page_size: int
    total_items: int
    total_pages: int
