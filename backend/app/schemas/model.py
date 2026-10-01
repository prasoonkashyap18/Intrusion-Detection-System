"""API schemas for ML model metadata.

Independent of app.models.model_metadata.ModelMetadata (the ORM model) by
design — see "Schema Separation" in backend/README.md.

Security / data-exposure decision:
`artifact_path` (the ORM field holding the internal filesystem path or
storage identifier for a saved model artifact) is intentionally NOT
included in ModelMetadataResponse. It is an internal implementation
detail with no legitimate frontend/dashboard use case in the MVP, and
exposing raw server-side paths over a public API needlessly reveals
internal filesystem/storage layout. The ORM field itself is unaffected —
only the public response schema omits it. See backend/README.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ModelMetadataResponse(BaseModel):
    # protected_namespaces=() because `model_name`/`model_version`/
    # `model_type` would otherwise collide with Pydantic's reserved
    # "model_" attribute namespace.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    model_name: str
    model_version: str
    model_type: str | None = None
    dataset_name: str | None = None
    dataset_version: str | None = None
    feature_set_version: str | None = None
    trained_at: datetime | None = None
    # Nullable — must never be fabricated. Null until a real model has
    # actually been trained and evaluated.
    evaluation_metrics: dict[str, Any] | None = None
    is_active: bool
    created_at: datetime
