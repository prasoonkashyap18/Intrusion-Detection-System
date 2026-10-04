"""API schemas for detection results.

Independent of app.models.detection_result.DetectionResult (the ORM
model) by design — see "Schema Separation" in backend/README.md. Mirrors
that model's real fields (Step 27) plus the model identity its
relationship already carries (Step 28) — never a guessed or computed
field. See backend/README.md's "DetectionResult API" section for the
endpoints these schemas back.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DetectionResultBase(BaseModel):
    row_number: int
    predicted_label: int = Field(ge=0, le=1)
    prediction_name: str
    attack_probability: float | None = Field(default=None, ge=0.0, le=1.0)


class DetectionResultResponse(DetectionResultBase):
    # protected_namespaces=() because `model_id`/`model_name`/`model_version`
    # would otherwise collide with Pydantic's reserved "model_" namespace.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    batch_id: uuid.UUID
    model_id: uuid.UUID
    # Read through the existing DetectionResult.model relationship (Step
    # 27) — never duplicated storage, never a second source of truth for
    # what a model is named/versioned.
    model_name: str
    model_version: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops timezone info on read. Every stored timestamp is
        # UTC, so restore it; otherwise clients would parse the value as
        # local time — same convention as app.schemas.batch.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class DetectionResultListResponse(BaseModel):
    """One page of a single batch's DetectionResult rows, ordered by
    row_number. An empty collection is a normal 200 response with
    `items: []` and `total_pages: 0` — the same shape
    DetectionBatchListResponse already uses for batch listing."""

    model_config = ConfigDict(protected_namespaces=())

    items: list[DetectionResultResponse]
    batch_id: uuid.UUID
    page: int
    page_size: int
    total_items: int
    total_pages: int
