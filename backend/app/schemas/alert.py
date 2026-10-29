"""API schemas for alerts (Step 36/37).

Independent of `app.models.alert.Alert` (the ORM model) by design — see
"Schema Separation" in backend/README.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import AlertStatus, Severity


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    batch_id: uuid.UUID
    row_number: int
    detection_result_id: uuid.UUID | None
    anomaly_result_id: uuid.UUID | None
    hybrid_result_id: uuid.UUID | None
    risk_assessment_id: uuid.UUID | None
    explanation_id: uuid.UUID | None
    title: str
    description: str
    severity: Severity
    status: AlertStatus
    evidence: dict
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class AlertListResponse(BaseModel):
    """One page of alerts. An empty collection is a normal 200 response
    with `items: []` and `total_pages: 0`, the same shape every other
    list endpoint in this API already uses."""

    items: list[AlertResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class AlertStatusUpdateRequest(BaseModel):
    status: AlertStatus
    note: str | None = Field(default=None, max_length=2000)


class AlertStatusHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    previous_status: AlertStatus
    new_status: AlertStatus
    note: str | None
    changed_at: datetime

    @field_validator("changed_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class AlertStatusHistoryResponse(BaseModel):
    alert_id: uuid.UUID
    items: list[AlertStatusHistoryEntry]
