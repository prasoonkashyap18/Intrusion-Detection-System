"""ThreatIntelLookup: one persisted, cached result of an external
threat-intelligence provider lookup for one supported indicator (an IP
address — the only indicator type anything in this codebase currently
extracts from persisted data; see `app.services.threat_intel`).

Step 40 is the first step that creates rows here. Rows are a cache, not
a detection signal: nothing in `app.services.alert_generation`/
`risk_scoring`/`hybrid_detection` reads this table, and it never alters
a `DetectionResult`/`AnomalyResult`/`Alert` row. `alert_id` is nullable
and only for traceability (which alert triggered this particular
lookup, when any did) — the cache itself is keyed by `(provider,
indicator)`, since the same IP can legitimately appear across many
alerts and must not be looked up again for each one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base


class ThreatIntelLookup(Base):
    __tablename__ = "threat_intel_lookups"
    __table_args__ = (
        # The cache key: at most one cached row per (provider, indicator)
        # pair. app.services.threat_intel's cache layer updates this row
        # in place (new `looked_up_at`/`result`) on a fresh lookup rather
        # than inserting a second one, so repeated lookups for the same
        # indicator never create unbounded rows.
        UniqueConstraint("provider_name", "indicator", name="uq_threat_intel_lookups_provider_indicator"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("alerts.id"), nullable=True, index=True
    )

    provider_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    indicator: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    indicator_type: Mapped[str] = mapped_column(String(20), nullable=False)
    """`"ip"` is the only value this step ever produces — see the module
    docstring. Kept as a column (rather than assumed) so a future
    indicator type is additive, not a breaking change."""

    # True only for a genuine, successfully parsed provider response —
    # never set for a timeout, a rate limit, a missing-credential
    # skip, or any other unavailable-lookup outcome (see `found` below
    # for the found/not-found distinction once a response WAS obtained).
    response_received: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Only meaningful when response_received is True: whether the
    # provider reported a genuine match for this indicator. NULL
    # whenever no response was ever obtained — never coerced to False,
    # which would wrongly look identical to a confirmed negative result.
    found: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reputation: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The provider's response, normalized but otherwise preserved —
    # never padded with invented fields the provider did not return.
    raw_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    unavailable_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Set only when `response_received` is False — e.g. `"missing_credentials"`,
    `"timeout"`, `"rate_limited"`, `"provider_error"` — never a filesystem
    path, stack trace, or raw exception text."""

    looked_up_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    alert: Mapped["Alert | None"] = relationship("Alert")
