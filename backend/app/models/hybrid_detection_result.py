"""HybridDetectionResult: one persisted combination of a batch/row's
supervised `DetectionResult` and (when available) its `AnomalyResult`,
produced by `app.services.hybrid_detection`.

Step 33 is the first step that creates rows here. A *separate* table
from both `DetectionResult` and `AnomalyResult`, not a column added to
either: this table records a derived judgment about how the two
independent signals relate to each other, never the signals themselves
(those stay exactly as persisted in Steps 27/32). Nothing here is
re-predicted — every row is built strictly from already-persisted
`DetectionResult`/`AnomalyResult` rows for the same `(batch_id,
row_number)`, matched by that pair alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

VALID_HYBRID_OUTCOMES = (
    "confirmed_attack",
    "supervised_attack_only",
    "anomaly_only",
    "clean",
)


class HybridDetectionResult(Base):
    __tablename__ = "hybrid_detection_results"
    __table_args__ = (
        # One hybrid outcome per (batch, row) — the same duplicate-
        # prevention pattern DetectionResult/AnomalyResult already use.
        UniqueConstraint("batch_id", "row_number", name="uq_hybrid_detection_results_batch_row"),
        CheckConstraint(
            "outcome IN ('confirmed_attack', 'supervised_attack_only', 'anomaly_only', 'clean')",
            name="ck_hybrid_detection_results_outcome",
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

    # The same row_number DetectionResult/AnomalyResult/MappedFeatureRecord
    # already use for this batch.
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Nullable: a row may lack a supervised or an anomaly result (the
    # latter is always best-effort — see app.services.batch_processor).
    # Kept as direct FKs (rather than re-deriving from batch_id+row_number
    # at read time) so a hybrid row traces back to the exact source rows
    # it was built from.
    detection_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("detection_results.id"), nullable=True, index=True
    )
    anomaly_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("anomaly_results.id"), nullable=True, index=True
    )

    supervised_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    anomaly_available: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # One of VALID_HYBRID_OUTCOMES — see app.services.hybrid_detection for
    # the exact, deterministic rule that produces it from the two signals.
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Plain, structured facts about what fed into `outcome` (e.g.
    # ["supervised:attack", "anomaly:flagged"]) — never a prose
    # explanation (see app.services.explainability, Step 35, for that) and
    # never a new numeric score; this is simply which raw facts were true.
    contributing_signals: Mapped[list] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch", back_populates="hybrid_results")
    detection_result: Mapped["DetectionResult | None"] = relationship("DetectionResult")
    anomaly_result: Mapped["AnomalyResult | None"] = relationship("AnomalyResult")
