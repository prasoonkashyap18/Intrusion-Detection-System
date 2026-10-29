"""API schemas for live capture sessions (Step 42).

Independent of `app.models.capture_session.CaptureSession` (the ORM
model) by design — see "Schema Separation" in backend/README.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import CaptureSessionStatus


class StartCaptureRequest(BaseModel):
    interface: str = Field(min_length=1, max_length=100, description="The network interface to capture from.")
    bpf_filter: str | None = Field(default=None, max_length=255, description="An optional BPF filter expression.")
    idle_timeout_seconds: float | None = Field(
        default=None, gt=0, description="Flow idle timeout; defaults to the server's configured value."
    )
    max_queue_size: int | None = Field(default=None, gt=0, description="Bounded packet-queue size.")


class CaptureSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    batch_id: uuid.UUID
    interface_name: str
    bpf_filter: str | None
    idle_timeout_seconds: float
    status: CaptureSessionStatus
    error_message: str | None
    stats: dict
    started_at: datetime
    stopped_at: datetime | None

    @field_validator("started_at")
    @classmethod
    def _assume_utc_started(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @field_validator("stopped_at")
    @classmethod
    def _assume_utc_stopped(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class CaptureSessionListResponse(BaseModel):
    items: list[CaptureSessionResponse]


class InterfaceListResponse(BaseModel):
    interfaces: list[str]
