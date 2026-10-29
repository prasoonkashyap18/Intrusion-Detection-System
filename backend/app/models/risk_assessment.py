"""RiskAssessment: one persisted, deterministic risk score + severity for
a batch/row's `HybridDetectionResult`, produced by `app.services.
risk_scoring`.

Step 34 is the first step that creates rows here. Deliberately a
*separate* table from `DetectionResult`, `AnomalyResult`, and
`HybridDetectionResult` — a risk score and severity are a derived,
configurable judgment computed *from* those rows, never a replacement
for them, and the original `attack_probability`/`anomaly_score` values
stay exactly as persisted in Steps 27/32 regardless of what this table
says. `risk_score` is bounded to `[0, 100]` by construction (see
`app.services.risk_scoring.compute_risk_score`) and `severity` is a
transparent, rule-based mapping of that score — neither is ever an
output of a trained model.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import Severity

VALID_SUPERVISED_COMPONENT_SOURCES = ("attack_probability", "predicted_label", "unavailable")


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        # One risk assessment per (batch, row) — same duplicate-prevention
        # pattern as DetectionResult/AnomalyResult/HybridDetectionResult.
        UniqueConstraint("batch_id", "row_number", name="uq_risk_assessments_batch_row"),
        CheckConstraint("risk_score >= 0.0 AND risk_score <= 100.0", name="ck_risk_assessments_risk_score_range"),
        CheckConstraint(
            "supervised_component IS NULL OR (supervised_component >= 0.0 AND supervised_component <= 1.0)",
            name="ck_risk_assessments_supervised_component_range",
        ),
        CheckConstraint(
            "anomaly_component IS NULL OR (anomaly_component >= 0.0 AND anomaly_component <= 1.0)",
            name="ck_risk_assessments_anomaly_component_range",
        ),
        CheckConstraint(
            "supervised_component_source IN ('attack_probability', 'predicted_label', 'unavailable')",
            name="ck_risk_assessments_supervised_component_source",
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
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    hybrid_result_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hybrid_detection_results.id"), nullable=False, index=True
    )

    # Bounded [0, 100] by construction in app.services.risk_scoring — the
    # CheckConstraint above is defense in depth, not the only guarantee.
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)

    severity: Mapped[Severity] = mapped_column(
        SAEnum(
            Severity,
            name="risk_severity",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    # The normalized [0, 1] contribution each signal made to risk_score,
    # and where the supervised one came from — NULL exactly when that
    # signal was unavailable (never coerced to 0.0; see the module
    # docstring and app.services.risk_scoring for why a missing signal
    # must not silently look like "definitely not risky").
    supervised_component: Mapped[float | None] = mapped_column(Float, nullable=True)
    supervised_component_source: Mapped[str] = mapped_column(String(20), nullable=False)
    anomaly_component: Mapped[float | None] = mapped_column(Float, nullable=True)

    # True only when both signals were available and agreed (the
    # "confirmed_attack" hybrid outcome) — see app.services.risk_scoring.
    agreement_bonus_applied: Mapped[bool] = mapped_column(Boolean, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch", back_populates="risk_assessments")
    hybrid_result: Mapped["HybridDetectionResult"] = relationship("HybridDetectionResult")
