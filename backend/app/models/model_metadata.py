"""ModelMetadata: identifies a trained ML model used for inference.

No records are created here — this table is populated once an actual
model is trained in a later step. Evaluation metrics are nullable and
must never be fabricated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base


class ModelMetadata(Base):
    __tablename__ = "model_metadata"
    __table_args__ = (
        UniqueConstraint("model_name", "model_version", name="uq_model_metadata_name_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    dataset_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    feature_set_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Nullable until an actual model has been trained and evaluated.
    # Must only ever hold real, measured evaluation results.
    evaluation_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    results: Mapped[list["DetectionResult"]] = relationship(
        "DetectionResult",
        back_populates="model",
        # No cascade delete: a model version being retired must not
        # silently delete the detection history it produced.
    )
