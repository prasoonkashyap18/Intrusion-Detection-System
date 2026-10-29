"""DetectionExplanation: one persisted, structured explanation of a
batch/row's `HybridDetectionResult` + `RiskAssessment`, produced by
`app.services.explainability`.

Step 35 is the first step that creates rows here. Deliberately a
*separate* table — an explanation is a structured account of what the
other tables already say (and, when genuinely available, the trained
supervised model's own global feature importances), never a new
detection signal, never a new score, and never a model's "reasoning" in
any causal sense a tree-ensemble classifier cannot actually provide.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base


class DetectionExplanation(Base):
    __tablename__ = "detection_explanations"
    __table_args__ = (
        # One explanation per (batch, row) — same duplicate-prevention
        # pattern as the other per-row tables.
        UniqueConstraint("batch_id", "row_number", name="uq_detection_explanations_batch_row"),
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
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    hybrid_result_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hybrid_detection_results.id"), nullable=False, index=True
    )
    risk_assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_assessments.id"), nullable=False, index=True
    )

    # Structured, not prose: {"measured_values": {...}, "rule_based_explanations": [...],
    # "unavailable_information": [...], "feature_importance": [...] | null}.
    # See app.services.explainability.Explanation for the exact shape and
    # the rule that each key may only ever contain a real measured value,
    # a genuine rule-based statement about an already-computed outcome, or
    # an explicit statement that something is unavailable — never a
    # fabricated feature importance or causal claim.
    explanation: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch", back_populates="explanations")
    hybrid_result: Mapped["HybridDetectionResult"] = relationship("HybridDetectionResult")
    risk_assessment: Mapped["RiskAssessment"] = relationship("RiskAssessment")
