"""API schemas for detection batches.

Independent of app.models.detection_batch.DetectionBatch (the ORM model)
by design — see "Schema Separation" in backend/README.md. No upload
request schema exists yet, since the upload endpoint is not implemented.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

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
