"""API schemas for detection results.

Independent of app.models.detection_result.DetectionResult (the ORM
model) by design — see "Schema Separation" in backend/README.md. No
guessed ML feature columns are included; only the fields the ORM model
itself defines.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import Severity


class DetectionResultBase(BaseModel):
    predicted_class: str
    confidence: float = Field(ge=0.0, le=1.0)
    severity: Severity

    # Optional flow-context fields. Nullable because not every dataset
    # provides all of them; no particular dataset's schema is assumed.
    source_ip: str | None = None
    destination_ip: str | None = None
    source_port: int | None = None
    destination_port: int | None = None
    protocol: str | None = None
    flow_timestamp: datetime | None = None


class DetectionResultResponse(DetectionResultBase):
    # protected_namespaces=() because `model_id` would otherwise collide
    # with Pydantic's reserved "model_" attribute namespace.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    batch_id: uuid.UUID
    model_id: uuid.UUID
    created_at: datetime
