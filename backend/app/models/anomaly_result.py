"""AnomalyResult: one persisted anomaly-detection outcome from
`app.services.anomaly_inference.predict_anomalies_with_model`, for one
row of one batch's persisted `MappedFeatureRecord` data.

Step 32 is the first step that creates rows here. Deliberately a
*separate* table from `DetectionResult`, not a column added to it: an
anomaly result and a supervised BENIGN/ATTACK prediction come from two
independent model families (`app.services.anomaly_detection_training`
vs. `app.services.model_training`), are produced by two independent,
best-effort-vs-required pipeline stages (see `app.services.
batch_processor._run_anomaly_detection`), and must never be conflated —
an `is_anomaly`/`anomaly_score` pair is a statistical-outlier judgment
from `IsolationForest`, not a probability, not a risk or severity score,
and not a claim that a row represents an attack; merging it into
`DetectionResult` would make that distinction easy to lose. The full
feature vector is deliberately NOT duplicated here either — it already
exists on `MappedFeatureRecord`, reachable via `batch_id` + `row_number`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base


class AnomalyResult(Base):
    __tablename__ = "anomaly_results"
    __table_args__ = (
        # One anomaly result per (batch, row) — mirrors
        # DetectionResult's own uq_detection_results_batch_row constraint
        # and rationale: a second insert attempt for the same pair (e.g. a
        # retried persistence call) is rejected at the database level
        # rather than silently duplicated.
        UniqueConstraint("batch_id", "row_number", name="uq_anomaly_results_batch_row"),
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

    # The same row_number MappedFeatureRecord (and DetectionResult) already
    # use for this batch — the link back to "which original CSV row"
    # without duplicating any of that row's feature values here.
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # True when app.services.anomaly_inference's IsolationForest.predict()
    # returned -1 for this row (unusually isolated relative to the BENIGN
    # training baseline) — never a claim that the row is malicious.
    is_anomaly: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)

    # The model's own real decision_function() output — lower means more
    # isolated/anomalous. Not a probability (no [0, 1] bound, unlike
    # DetectionResult.attack_probability), not a percentage, and not a
    # risk/severity score; stored exactly as the model computed it. Always
    # a real, finite number — app.services.anomaly_inference already
    # validates finiteness before this table ever sees it, so unlike
    # attack_probability there is no NULL/missing case to represent here.
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch", back_populates="anomaly_results")
    model: Mapped["ModelMetadata"] = relationship("ModelMetadata", back_populates="anomaly_results")

    @property
    def model_name(self) -> str:
        """Read through the existing `model` relationship — mirrors
        `DetectionResult.model_name`'s own convenience property."""
        return self.model.model_name

    @property
    def model_version(self) -> str:
        return self.model.model_version
