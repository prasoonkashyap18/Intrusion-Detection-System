"""ModelMetadata: the model registry — identifies a trained ML model.

Step 22 is the first step that creates rows here, via
`app.services.model_training`. A row is only ever written *after* its
artifact has been saved successfully (see that module's transaction-safety
notes) — this table never represents a model as usable before its file on
disk actually exists. Evaluation metrics are nullable and must only ever
hold real, measured validation results — never a fabricated or assumed
production accuracy claim.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import ModelStatus


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

    # The dataset adapter schema_id (e.g. "nsl-kdd-style") the training data
    # was recognized as, not a free-text dataset name — see
    # app.services.dataset_adapters. dataset_version is left unused for now;
    # no per-dataset versioning concept exists yet.
    dataset_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    feature_set_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Nullable until an actual model has been trained and evaluated.
    # Must only ever hold real, measured evaluation results.
    evaluation_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[ModelStatus] = mapped_column(
        SAEnum(
            ModelStatus,
            name="model_status",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ModelStatus.READY,
    )

    # Server-generated training configuration (split ratio, random seed,
    # preprocessing strategy, model hyperparameters) — never accepted from
    # API input, and never containing a filesystem path.
    training_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Batch IDs (as strings) whose persisted MappedFeatureRecord rows were
    # used to train this model — traceability back to the training data
    # without duplicating it here.
    training_batch_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

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
