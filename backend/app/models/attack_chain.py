"""AttackChain / AttackChainEvent: a persisted, deduplicated connected
group of `Alert` rows linked by one or more `EventCorrelation` edges,
produced by `app.services.attack_chain_analysis`.

Step 39 is the first step that creates rows here. Deliberately named
and classified conservatively: `classification` (`CORROBORATED` vs
`HEURISTIC`, see `app.models.enums.ChainClassification`) describes only
the strength of the correlation evidence connecting the chain's
members, never a confirmed-campaign claim, a MITRE ATT&CK technique, or
an attack stage — none of those are computed anywhere in this codebase,
so none are stored here. This table never writes to `Alert.status`;
chain analysis is read-only with respect to the alert lifecycle (Step
37 owns that exclusively) — see `app.services.attack_chain_analysis`'s
module docstring.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import ChainClassification


class AttackChain(Base):
    __tablename__ = "attack_chains"
    __table_args__ = (
        # The entire chain-level deduplication strategy: `member_signature`
        # is a deterministic hash of the chain's sorted member alert ids
        # (see app.services.attack_chain_analysis.compute_member_signature).
        # Re-analyzing the same underlying evidence always recomputes the
        # same signature, so a second attempt to persist the same member
        # set is rejected here rather than creating a duplicate chain.
        UniqueConstraint("member_signature", name="uq_attack_chains_member_signature"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    member_signature: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    classification: Mapped[ChainClassification] = mapped_column(
        SAEnum(
            ChainClassification,
            name="chain_classification",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    # A plain summary of which correlation rules/types connect this
    # chain's members (e.g. {"correlation_types": ["shared_source_ip"],
    # "member_count": 3}) — aggregated from the real EventCorrelation
    # rows that justify this chain, never invented. See
    # app.services.attack_chain_analysis for the exact shape.
    evidence_summary: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    events: Mapped[list["AttackChainEvent"]] = relationship(
        "AttackChainEvent", back_populates="chain", order_by="AttackChainEvent.sequence_index"
    )


class AttackChainEvent(Base):
    """One `Alert`'s membership in one `AttackChain`, with its
    deterministic position in that chain's observed ordering."""

    __tablename__ = "attack_chain_events"
    __table_args__ = (
        # An alert belongs to a given chain at most once.
        UniqueConstraint("chain_id", "alert_id", name="uq_attack_chain_events_chain_alert"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    chain_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("attack_chains.id"), nullable=False, index=True
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("alerts.id"), nullable=False, index=True
    )

    # 0-based position within this chain, assigned deterministically by
    # sorting member alerts on (created_at, id) — see
    # app.services.attack_chain_analysis. Never implies a confirmed
    # causal sequence, only an observed chronological ordering.
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)

    chain: Mapped["AttackChain"] = relationship("AttackChain", back_populates="events")
    alert: Mapped["Alert"] = relationship("Alert")
