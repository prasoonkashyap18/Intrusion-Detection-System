"""Alert: one persisted, rule-triggered notice that a traffic row's
combined detection signals warrant human attention, produced by
`app.services.alert_generation`.

Step 36 is the first step that creates rows here. Deliberately a
*separate* table from `DetectionResult`/`AnomalyResult`/
`HybridDetectionResult`/`RiskAssessment`/`DetectionExplanation`: an
alert is a decision to surface a row, never a new detection signal and
never a rewrite of an existing one. Every reference below is a nullable
FK to an already-persisted row, never a duplicated copy of its values —
`evidence` stores only a structured snapshot of facts those rows
already contain (mirroring `DetectionExplanation.explanation`'s own
"never fabricate" discipline), so an alert can always be traced back to
the exact rows that justified it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import AlertStatus, Severity


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        # One alert per (batch, row) — the entire deduplication
        # strategy for Step 36's "prevent duplicate alerts when
        # processing is retried or the same eligible record is
        # evaluated repeatedly": a retried alert-generation attempt for
        # a row that already has an alert is rejected at the database
        # level, mirroring every other per-row table in this pipeline.
        UniqueConstraint("batch_id", "row_number", name="uq_alerts_batch_row"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("detection_batches.id"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Nullable FKs to the exact rows this alert was built from — never a
    # duplicated copy of their contents. All four may be unavailable in
    # principle (anomaly/hybrid/risk/explanation are each best-effort
    # upstream stages); supervised detection_result_id is only ever
    # unset for the same "row with no supervised result" edge case
    # app.services.hybrid_detection already documents handling safely.
    detection_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("detection_results.id"), nullable=True, index=True
    )
    anomaly_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("anomaly_results.id"), nullable=True, index=True
    )
    hybrid_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hybrid_detection_results.id"), nullable=True, index=True
    )
    risk_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_assessments.id"), nullable=True, index=True
    )
    explanation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("detection_explanations.id"), nullable=True, index=True
    )

    # Short, templated text — never free-text model output. See
    # app.services.alert_generation for the exact, reviewed templates.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    severity: Mapped[Severity] = mapped_column(
        SAEnum(
            Severity,
            name="alert_severity",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(
            AlertStatus,
            name="alert_status",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=AlertStatus.OPEN,
        index=True,
    )

    # A structured snapshot of the facts that justified this alert —
    # hybrid outcome, contributing signals, risk score/severity, and the
    # already-built DetectionExplanation payload when available. Never a
    # new fact, never a fabricated indicator/IP/timestamp; see the
    # module docstring.
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch", back_populates="alerts")
    detection_result: Mapped["DetectionResult | None"] = relationship("DetectionResult")
    anomaly_result: Mapped["AnomalyResult | None"] = relationship("AnomalyResult")
    hybrid_result: Mapped["HybridDetectionResult | None"] = relationship("HybridDetectionResult")
    risk_assessment: Mapped["RiskAssessment | None"] = relationship("RiskAssessment")
    explanation: Mapped["DetectionExplanation | None"] = relationship("DetectionExplanation")
    status_history: Mapped[list["AlertStatusHistory"]] = relationship(
        "AlertStatusHistory", back_populates="alert", order_by="AlertStatusHistory.changed_at"
    )
