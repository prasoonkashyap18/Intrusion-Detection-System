"""EventCorrelation: one persisted, evidence-based edge between two
`Alert` rows, produced by `app.services.event_correlation`.

Step 38 is the first step that creates rows here. An edge, not a group:
`app.services.attack_chain_analysis` (Step 39) builds chains by finding
connected components over these edges — this table deliberately stays a
simple pairwise relationship so that later step can be built without
this one needing to anticipate it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import CorrelationStrength

VALID_CORRELATION_TYPES = ("shared_source_ip", "shared_destination_ip", "same_batch_proximity")


class EventCorrelation(Base):
    __tablename__ = "event_correlations"
    __table_args__ = (
        # One edge per (pair, rule) — `app.services.event_correlation`
        # always stores `first_alert_id < second_alert_id` (string
        # comparison of the UUIDs) before insert, so a re-run that finds
        # the same pair/rule again is rejected here rather than silently
        # duplicated or stored as the reverse pair.
        UniqueConstraint(
            "first_alert_id", "second_alert_id", "correlation_type", name="uq_event_correlations_pair_type"
        ),
        CheckConstraint(
            "correlation_type IN ('shared_source_ip', 'shared_destination_ip', 'same_batch_proximity')",
            name="ck_event_correlations_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    first_alert_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("alerts.id"), nullable=False, index=True
    )
    second_alert_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("alerts.id"), nullable=False, index=True
    )

    # One of VALID_CORRELATION_TYPES — see app.services.event_correlation
    # for the exact, documented rule each one implements.
    correlation_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    strength: Mapped[CorrelationStrength] = mapped_column(
        SAEnum(
            CorrelationStrength,
            name="correlation_strength",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    # The real, matched facts behind this edge (e.g. {"shared_value":
    # "10.0.0.5", "time_delta_seconds": 42.0}) — never a confidence
    # score, never an invented attribute. See app.services.
    # event_correlation.CorrelationCandidate for the exact shape per type.
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    first_alert: Mapped["Alert"] = relationship("Alert", foreign_keys="EventCorrelation.first_alert_id")
    second_alert: Mapped["Alert"] = relationship("Alert", foreign_keys="EventCorrelation.second_alert_id")
