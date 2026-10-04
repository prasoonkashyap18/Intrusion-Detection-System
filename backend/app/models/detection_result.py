"""DetectionResult: one persisted prediction from `app.services.
model_inference.predict_with_model`, for one row of one batch's
persisted `MappedFeatureRecord` data.

Step 27 is the first step that actually creates rows here — earlier
revisions of this table carried placeholder MVP fields (`confidence`,
`severity`, flow-context columns) that nothing in the real pipeline ever
populated; this redesign replaces them with the fields the existing
training/evaluation/inference architecture (Steps 22-26) actually
produces. The full feature vector is deliberately NOT duplicated here —
it already exists on `MappedFeatureRecord`, reachable via `batch_id` +
`row_number`; this table stores only the prediction outcome.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base


class DetectionResult(Base):
    __tablename__ = "detection_results"
    __table_args__ = (
        # One prediction per (batch, row) — the row_number this result
        # describes was already unique within its batch at the
        # MappedFeatureRecord stage; a second insert attempt for the same
        # pair (e.g. a retried persistence call) is rejected at the
        # database level rather than silently duplicated.
        UniqueConstraint("batch_id", "row_number", name="uq_detection_results_batch_row"),
        CheckConstraint("predicted_label IN (0, 1)", name="ck_detection_results_predicted_label"),
        CheckConstraint("prediction_name IN ('benign', 'attack')", name="ck_detection_results_prediction_name"),
        CheckConstraint(
            "attack_probability IS NULL OR (attack_probability >= 0.0 AND attack_probability <= 1.0)",
            name="ck_detection_results_attack_probability_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("detection_batches.id"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("model_metadata.id"),
        nullable=False,
        index=True,
    )

    # The same row_number MappedFeatureRecord already uses for this batch
    # — the link back to "which original CSV row" without duplicating any
    # of that row's feature values here. Deterministic ordering (never
    # dict/insertion order) follows from this column, exactly as it does
    # for MappedFeatureRecord.
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # BinaryLabel's own values (app.services.dataset_adapters.
    # label_mapping.BinaryLabel): 0 = BENIGN, 1 = ATTACK. Stored as a
    # plain validated integer (CheckConstraint above) rather than a new
    # SQLAlchemy enum — there are only ever these two legitimate values,
    # and app.services.model_inference already validates a prediction
    # against exactly this set before this table ever sees it.
    predicted_label: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # The canonical "benign"/"attack" name for predicted_label — stored
    # alongside it (not derived at query time) so a row is self-describing
    # without a caller needing to know BinaryLabel's numeric convention.
    prediction_name: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    # The probability of the ATTACK class, when app.services.
    # model_inference genuinely obtained one from the model's own
    # predict_proba — NULL, never a fabricated placeholder like 0.0 or
    # 0.5, whenever the model did not support or could not produce one.
    # This NULL/real distinction is the entire point of this column being
    # nullable: a missing probability must never be indistinguishable from
    # "the model reported zero probability of attack."
    attack_probability: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch", back_populates="results")
    model: Mapped["ModelMetadata"] = relationship("ModelMetadata", back_populates="results")
