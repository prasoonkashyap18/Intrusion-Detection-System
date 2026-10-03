"""DetectionBatch: represents one uploaded CSV processing job."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import ProcessingStatus


class DetectionBatch(Base):
    __tablename__ = "detection_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[ProcessingStatus] = mapped_column(
        SAEnum(
            ProcessingStatus,
            name="processing_status",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ProcessingStatus.PENDING,
    )

    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list["DetectionResult"]] = relationship(
        "DetectionResult",
        back_populates="batch",
        # No cascade delete: detection history must not disappear silently
        # if a batch row is removed. Deleting a batch with results is left
        # to a deliberate, explicit operation designed in a later step.
    )

    mapped_feature_records: Mapped[list["MappedFeatureRecord"]] = relationship(
        "MappedFeatureRecord",
        back_populates="batch",
        # No cascade delete, consistent with `results` above: persisted
        # feature history must not disappear silently if a batch row is
        # removed.
    )
