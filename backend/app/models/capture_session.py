"""CaptureSession: one live packet-capture session (Step 42), linked to
the `DetectionBatch` whose `MappedFeatureRecord`/detection/alert rows
its finalized flows are persisted under (see
`app.services.live_capture`).

A new table rather than reusing `DetectionBatch` directly for the
session concept itself: a capture session has its own lifecycle
(`running`/`stopped`/`failed`, started/stopped independently of any
single batch's pending/processing/completed/failed states) and its own
operational statistics (packets received/processed/dropped, flows
created/finalized, parse failures) that no existing table represents.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import CaptureSessionStatus


class CaptureSession(Base):
    __tablename__ = "capture_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("detection_batches.id"), nullable=False, index=True
    )

    interface_name: Mapped[str] = mapped_column(String(100), nullable=False)
    bpf_filter: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """An optional Berkeley Packet Filter expression, applied by the
    capture backend itself (never evaluated or interpreted by this
    codebase) — e.g. `"tcp port 443"`. `None` means "no filter",
    *never* "capture everything by default without an explicit choice"
    — see `app.services.live_capture.start_capture`, which always
    requires an explicit `interface`."""

    idle_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[CaptureSessionStatus] = mapped_column(
        SAEnum(
            CaptureSessionStatus,
            name="capture_session_status",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=CaptureSessionStatus.RUNNING,
        index=True,
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Real, measured counters only — see app.services.live_capture for
    # exactly when each is incremented. Never a fabricated/estimated
    # value; absent concepts for a given backend are simply left at 0,
    # documented explicitly where that applies (see "packets_dropped").
    stats: Mapped[dict] = mapped_column(JSON, nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch")
