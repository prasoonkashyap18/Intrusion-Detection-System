"""API schemas for detection batches.

Independent of app.models.detection_batch.DetectionBatch (the ORM model)
by design — see "Schema Separation" in backend/README.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.enums import ProcessingStatus


class DetectionBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: ProcessingStatus
    total_records: int
    processed_records: int
    failed_records: int
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


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
