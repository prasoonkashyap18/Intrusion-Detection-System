"""Persisted cache for threat-intelligence lookups, keyed by
`(provider_name, indicator)` — see `app.models.threat_intel_lookup.
ThreatIntelLookup`'s own docstring for why a single IP must not be
looked up once per alert that happens to reference it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.threat_intel_lookup import ThreatIntelLookup
from app.services.threat_intel.base import ThreatIntelResult

logger = logging.getLogger("ai_ids")


class ThreatIntelPersistenceError(Exception):
    """Raised when a lookup result cannot be safely cached — never
    silently swallowed by this module itself (the caller — `app.
    services.threat_intel.enrichment` — decides whether that failure
    should interrupt anything, and it never does)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def get_cached_lookup(
    db: Session, provider_name: str, indicator: str, *, ttl_seconds: float
) -> ThreatIntelLookup | None:
    """Returns the cached row for `(provider_name, indicator)` if one
    exists and is within `ttl_seconds` of its own `looked_up_at` —
    `None` otherwise (no cached row, or the cached row has expired, in
    which case a fresh lookup is the caller's job, not this function's).
    """
    row = db.scalars(
        select(ThreatIntelLookup).where(
            ThreatIntelLookup.provider_name == provider_name, ThreatIntelLookup.indicator == indicator
        )
    ).first()
    if row is None:
        return None

    looked_up_at = row.looked_up_at if row.looked_up_at.tzinfo else row.looked_up_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - looked_up_at > timedelta(seconds=ttl_seconds):
        return None
    return row


def upsert_lookup(
    db: Session,
    result: ThreatIntelResult,
    *,
    alert_id: uuid.UUID | None,
) -> ThreatIntelLookup:
    """Writes `result` as the cached row for `(result.provider_name,
    result.indicator)` — updating the existing row in place if one
    already exists (so repeated lookups for the same indicator never
    create unbounded rows), inserting a new one otherwise. Commits in
    one transaction; a database error rolls back and raises
    `ThreatIntelPersistenceError`.
    """
    try:
        row = db.scalars(
            select(ThreatIntelLookup).where(
                ThreatIntelLookup.provider_name == result.provider_name,
                ThreatIntelLookup.indicator == result.indicator,
            )
        ).first()

        if row is None:
            row = ThreatIntelLookup(
                provider_name=result.provider_name,
                indicator=result.indicator,
                indicator_type=result.indicator_type,
            )
            db.add(row)

        row.alert_id = alert_id if alert_id is not None else row.alert_id
        row.response_received = result.response_received
        row.found = result.found
        row.confidence = result.confidence
        row.category = result.category
        row.reputation = result.reputation
        row.reference_url = result.reference_url
        row.raw_result = result.raw_result
        row.unavailable_reason = result.unavailable_reason
        row.looked_up_at = result.queried_at

        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist a threat-intelligence lookup")
        raise ThreatIntelPersistenceError("Unable to persist the threat-intelligence lookup.") from None

    return row


def result_from_cached_row(row: ThreatIntelLookup) -> ThreatIntelResult:
    """Reconstructs a `ThreatIntelResult` from a cached row — used when
    a fresh lookup is skipped because the cache already had a result
    within its TTL, so a caller never has to branch on "was this from
    cache or a live call" to use the result."""
    return ThreatIntelResult(
        indicator=row.indicator,
        indicator_type=row.indicator_type,
        provider_name=row.provider_name,
        response_received=row.response_received,
        found=row.found,
        confidence=row.confidence,
        category=row.category,
        reputation=row.reputation,
        reference_url=row.reference_url,
        raw_result=row.raw_result,
        unavailable_reason=row.unavailable_reason,
        queried_at=row.looked_up_at,
    )


__all__ = ["ThreatIntelPersistenceError", "get_cached_lookup", "upsert_lookup", "result_from_cached_row"]
