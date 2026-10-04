"""API schemas for detection results.

Independent of app.models.detection_result.DetectionResult (the ORM
model) by design — see "Schema Separation" in backend/README.md. Mirrors
that model's real fields (Step 27) rather than a guessed shape; no API
route is wired to these schemas yet (see backend/README.md's "Model
Inference in Batch Processing" / "DetectionResult Persistence" sections —
exposing predictions over an endpoint is explicitly a later step).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DetectionResultBase(BaseModel):
    row_number: int
    predicted_label: int = Field(ge=0, le=1)
    prediction_name: str
    attack_probability: float | None = Field(default=None, ge=0.0, le=1.0)


class DetectionResultResponse(DetectionResultBase):
    # protected_namespaces=() because `model_id` would otherwise collide
    # with Pydantic's reserved "model_" attribute namespace.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    batch_id: uuid.UUID
    model_id: uuid.UUID
    created_at: datetime
