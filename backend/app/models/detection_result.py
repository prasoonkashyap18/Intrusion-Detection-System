"""DetectionResult: the outcome of analyzing one network-flow record.

Deliberately dataset-independent: source/destination/protocol fields are
optional context, not a commitment to any specific IDS dataset's schema.
The full raw/processed ML feature vector is intentionally NOT stored as
columns on this table — if that needs preserving later, it will be
designed separately rather than guessed now.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import Severity


class DetectionResult(Base):
    __tablename__ = "detection_results"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_detection_results_confidence_range"
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

    # Dataset-independent predicted label (e.g. "normal", "dos", "probe").
    # The controlled set of possible values depends on the dataset chosen
    # in a later step, so this is intentionally a free string for now.
    predicted_class: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Model-reported confidence for the predicted class, expected range 0.0-1.0.
    # Range is enforced by the CheckConstraint above; the API layer will
    # additionally validate this before it reaches the database.
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Rule-based MVP severity derived from prediction + confidence — not an
    # ML-produced risk score.
    severity: Mapped[Severity] = mapped_column(
        SAEnum(
            Severity,
            name="detection_severity",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    # Optional flow-context fields. Nullable because not every dataset
    # provides all of them; no particular dataset's schema is assumed.
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    destination_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    source_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Timestamp from the original flow record, if the source data provides one.
    flow_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch", back_populates="results")
    model: Mapped["ModelMetadata"] = relationship("ModelMetadata", back_populates="results")
