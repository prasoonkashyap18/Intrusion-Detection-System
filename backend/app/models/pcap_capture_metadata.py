"""PcapCaptureMetadata: packet-capture-specific metadata and processing
statistics for one `DetectionBatch` that originated from an uploaded
`.pcap`/`.pcapng` file (Step 41), kept in a dedicated table rather than
new columns on `DetectionBatch` — this project's `Base.metadata.
create_all()` schema mechanism only ever creates missing tables, never
alters an existing one's columns (see backend/README.md's "Database"
section), so every step so far has added a new table instead; this is
the same pattern applied to PCAP-specific fields `DetectionBatch`
itself has no use for.

A row's mere existence for a given `batch_id` is what distinguishes a
PCAP-sourced batch from a CSV-sourced one — see
`app.services.batch_processor.process_batch`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

VALID_CAPTURE_FORMATS = ("pcap", "pcapng")


class PcapCaptureMetadata(Base):
    __tablename__ = "pcap_capture_metadata"
    __table_args__ = (
        # One metadata row per batch — a batch is either a CSV batch or
        # a PCAP batch, never both, and never represented twice.
        UniqueConstraint("batch_id", name="uq_pcap_capture_metadata_batch"),
        CheckConstraint("capture_format IN ('pcap', 'pcapng')", name="ck_pcap_capture_metadata_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("detection_batches.id"), nullable=False, index=True
    )

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    capture_format: Mapped[str] = mapped_column(String(10), nullable=False)

    idle_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    """The flow-finalization idle timeout actually used for this batch's
    packet-to-flow conversion (`app.services.flow_construction`) —
    recorded per batch, not just read from current configuration, so a
    later change to the default never makes an old batch's flow
    boundaries unexplainable."""

    # Populated by app.services.pcap_ingestion once processing has run;
    # NULL beforehand (a batch that is still `pending` has not been
    # parsed yet — never a fabricated 0 standing in for "not measured").
    packets_read: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packets_parsed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packets_skipped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Packets read but not usable for flow construction — no IP layer,
    or a layer this module could not safely parse — counted, never
    silently dropped without a trace."""
    flows_constructed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch")
