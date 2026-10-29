"""Event correlation: finds evidence-based relationships between
already-persisted `Alert` rows and persists them as `EventCorrelation`
edges.

    Alert rows (+ their MappedFeatureRecord, for IP evidence)
        |
    load_alert_contexts()
        |
    find_correlations()  <- pure, deterministic, configurable
        |
    persist_correlations()
        |
    EventCorrelation rows

This module performs NO new detection: its only inputs are `Alert` rows
`app.services.alert_generation` already persisted and the IP evidence
`MappedFeatureRecord` already persisted during feature extraction
(Step 17) — it never reads the batch's CSV again and never fabricates
a source/destination IP for a row that never had one. Two of the three
rules it implements require that genuine IP evidence; the third
requires none at all (see `CorrelationRule` below), so a row from a
dataset with no IP columns (e.g. NSL-KDD-style) can still participate
in the one rule that does not need it.

Three documented rules, each producing its own `EventCorrelation.
correlation_type`:

- `shared_source_ip` / `shared_destination_ip` (**STRONG** evidence):
  two alerts whose rows' `MappedFeatureRecord.features["source_ip_
  numeric"]["raw_value"]` (or `"destination_ip_numeric"`) are equal,
  non-null strings, within `config.time_window_seconds` of each other's
  `Alert.created_at`. An exact match on a real network attribute.
- `same_batch_proximity` (**WEAK** evidence): two alerts from the same
  batch whose `row_number`s differ by at most `config.max_row_distance`
  — a heuristic ("these rows were near each other in the same upload"),
  never claimed as strong evidence, and the only rule that needs no IP
  data at all.

Idempotency: `app.services.event_correlation.build_and_persist_
correlations_for_batch` is safe to call repeatedly for the same batch —
each candidate edge is checked against what already exists before
insert, and `EventCorrelation`'s own `UniqueConstraint("first_alert_id",
"second_alert_id", "correlation_type")` (with `first_alert_id` always
the lexicographically smaller UUID string — see `order_pair`) is the
database-level backstop against a race producing a duplicate.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.enums import CorrelationStrength
from app.models.event_correlation import EventCorrelation
from app.models.mapped_feature_record import MappedFeatureRecord

logger = logging.getLogger("ai_ids")

_SOURCE_IP_FEATURE = "source_ip_numeric"
_DESTINATION_IP_FEATURE = "destination_ip_numeric"


class EventCorrelationPersistenceError(Exception):
    """Raised when this module cannot safely persist a set of
    correlation edges — never partially, and never silently."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class EventCorrelationConfig:
    """One fixed, documented, overridable rule set — not learned, not
    fit to data, mirroring every other configurable module in this
    pipeline."""

    time_window_seconds: float = 300.0
    """Maximum `Alert.created_at` difference for an IP-based rule to
    match. Must be >= 0."""

    max_row_distance: int = 5
    """Maximum `row_number` difference, within the same batch, for the
    `same_batch_proximity` rule to match. Must be >= 1."""


DEFAULT_EVENT_CORRELATION_CONFIG = EventCorrelationConfig()


@dataclass(frozen=True)
class AlertContext:
    """One alert's correlation-relevant facts — the only input
    `find_correlations` needs. `source_ip`/`destination_ip` are the
    real, persisted `raw_value` strings when available, `None`
    otherwise (never fabricated)."""

    alert_id: uuid.UUID
    batch_id: uuid.UUID
    row_number: int
    created_at: datetime
    source_ip: str | None = None
    destination_ip: str | None = None


@dataclass(frozen=True)
class CorrelationCandidate:
    first_alert_id: uuid.UUID
    second_alert_id: uuid.UUID
    correlation_type: str
    strength: CorrelationStrength
    evidence: dict = field(default_factory=dict)


def order_pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Canonical ordering for a pair of alert ids — the entire basis for
    never storing both `(A, B)` and `(B, A)` as separate edges."""
    return (a, b) if str(a) < str(b) else (b, a)


def find_correlations(
    contexts: Sequence[AlertContext],
    new_alert_ids: set[uuid.UUID],
    config: EventCorrelationConfig = DEFAULT_EVENT_CORRELATION_CONFIG,
) -> list[CorrelationCandidate]:
    """Pure function: evaluates every pair in `contexts` against the
    three documented rules and returns one `CorrelationCandidate` per
    match. Identical inputs and `config` always yield the same result.

    Only pairs where at least one alert id is in `new_alert_ids` are
    considered — a pair of two alerts that were both already present
    before this call would already have been evaluated when the
    earlier of the two was itself new, so re-evaluating it here would
    be redundant work, not a new possibility to discover.
    """
    candidates: list[CorrelationCandidate] = []
    window = timedelta(seconds=config.time_window_seconds)

    for i, a in enumerate(contexts):
        for b in contexts[i + 1 :]:
            if a.alert_id not in new_alert_ids and b.alert_id not in new_alert_ids:
                continue

            first_id, second_id = order_pair(a.alert_id, b.alert_id)
            time_delta = abs((a.created_at - b.created_at).total_seconds())

            if a.source_ip is not None and a.source_ip == b.source_ip and time_delta <= window.total_seconds():
                candidates.append(
                    CorrelationCandidate(
                        first_alert_id=first_id,
                        second_alert_id=second_id,
                        correlation_type="shared_source_ip",
                        strength=CorrelationStrength.STRONG,
                        evidence={"shared_value": a.source_ip, "time_delta_seconds": time_delta},
                    )
                )

            if (
                a.destination_ip is not None
                and a.destination_ip == b.destination_ip
                and time_delta <= window.total_seconds()
            ):
                candidates.append(
                    CorrelationCandidate(
                        first_alert_id=first_id,
                        second_alert_id=second_id,
                        correlation_type="shared_destination_ip",
                        strength=CorrelationStrength.STRONG,
                        evidence={"shared_value": a.destination_ip, "time_delta_seconds": time_delta},
                    )
                )

            if a.batch_id == b.batch_id and a.row_number != b.row_number:
                row_distance = abs(a.row_number - b.row_number)
                if row_distance <= config.max_row_distance:
                    candidates.append(
                        CorrelationCandidate(
                            first_alert_id=first_id,
                            second_alert_id=second_id,
                            correlation_type="same_batch_proximity",
                            strength=CorrelationStrength.WEAK,
                            evidence={"row_distance": row_distance},
                        )
                    )

    return candidates


def _ip_raw_value(features: dict, feature_name: str) -> str | None:
    entry = features.get(feature_name)
    if not isinstance(entry, dict):
        return None
    raw_value = entry.get("raw_value")
    return raw_value if isinstance(raw_value, str) and raw_value else None


def load_alert_contexts(db: Session, alerts: Sequence[Alert]) -> list[AlertContext]:
    """Builds one `AlertContext` per alert, reading each alert's own
    `(batch_id, row_number)` `MappedFeatureRecord` for IP evidence —
    never another row's. An alert whose feature row has no IP columns
    (or no feature row at all — should not occur, but handled safely)
    simply gets `source_ip=None`/`destination_ip=None`.
    """
    if not alerts:
        return []

    keys = {(alert.batch_id, alert.row_number) for alert in alerts}
    batch_ids = {key[0] for key in keys}
    rows = db.scalars(select(MappedFeatureRecord).where(MappedFeatureRecord.batch_id.in_(batch_ids))).all()
    by_key = {(row.batch_id, row.row_number): row for row in rows}

    contexts = []
    for alert in alerts:
        feature_row = by_key.get((alert.batch_id, alert.row_number))
        source_ip = _ip_raw_value(feature_row.features, _SOURCE_IP_FEATURE) if feature_row is not None else None
        destination_ip = (
            _ip_raw_value(feature_row.features, _DESTINATION_IP_FEATURE) if feature_row is not None else None
        )
        contexts.append(
            AlertContext(
                alert_id=alert.id,
                batch_id=alert.batch_id,
                row_number=alert.row_number,
                created_at=alert.created_at,
                source_ip=source_ip,
                destination_ip=destination_ip,
            )
        )
    return contexts


def persist_correlations(db: Session, candidates: Sequence[CorrelationCandidate]) -> list[EventCorrelation]:
    """Persists one `EventCorrelation` row per candidate not already
    present (checked by `(first_alert_id, second_alert_id,
    correlation_type)` before insert). Returns `[]` without writing
    anything for an empty or fully-duplicate sequence. Commits all new
    rows in one transaction; a database error (including a race that
    slips past the pre-check) rolls back and raises
    `EventCorrelationPersistenceError` rather than leaving a partial set.
    """
    if not candidates:
        return []

    existing = db.scalars(
        select(EventCorrelation).where(
            or_(
                *[
                    (EventCorrelation.first_alert_id == c.first_alert_id)
                    & (EventCorrelation.second_alert_id == c.second_alert_id)
                    & (EventCorrelation.correlation_type == c.correlation_type)
                    for c in candidates
                ]
            )
        )
    ).all()
    existing_keys = {(row.first_alert_id, row.second_alert_id, row.correlation_type) for row in existing}

    new_rows = [
        EventCorrelation(
            first_alert_id=c.first_alert_id,
            second_alert_id=c.second_alert_id,
            correlation_type=c.correlation_type,
            strength=c.strength,
            evidence=c.evidence,
        )
        for c in candidates
        if (c.first_alert_id, c.second_alert_id, c.correlation_type) not in existing_keys
    ]

    if not new_rows:
        return []

    try:
        db.add_all(new_rows)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist event correlations")
        raise EventCorrelationPersistenceError("Unable to persist event correlations.") from None

    return new_rows


def build_and_persist_correlations_for_batch(
    db: Session,
    batch_id: uuid.UUID,
    config: EventCorrelationConfig = DEFAULT_EVENT_CORRELATION_CONFIG,
) -> list[EventCorrelation]:
    """Orchestration: loads this batch's own newly-generated `Alert`
    rows, loads a bounded pool of other alerts that could plausibly
    correlate with them (same batch — for the proximity rule — or
    within `config.time_window_seconds` of the new alerts' timestamps,
    across any batch — for the IP rules), evaluates every documented
    rule, and persists whatever new edges result. Returns `[]` (and
    persists nothing) when this batch produced no alerts.
    """
    new_alerts = list(db.scalars(select(Alert).where(Alert.batch_id == batch_id)).all())
    if not new_alerts:
        return []

    window = timedelta(seconds=config.time_window_seconds)
    min_created = min(a.created_at for a in new_alerts) - window
    max_created = max(a.created_at for a in new_alerts) + window

    pool_alerts = list(
        db.scalars(
            select(Alert).where(
                or_(
                    Alert.batch_id == batch_id,
                    (Alert.created_at >= min_created) & (Alert.created_at <= max_created),
                )
            )
        ).all()
    )

    contexts = load_alert_contexts(db, pool_alerts)
    new_alert_ids = {alert.id for alert in new_alerts}
    candidates = find_correlations(contexts, new_alert_ids, config)
    return persist_correlations(db, candidates)


__all__ = [
    "EventCorrelationPersistenceError",
    "EventCorrelationConfig",
    "DEFAULT_EVENT_CORRELATION_CONFIG",
    "AlertContext",
    "CorrelationCandidate",
    "order_pair",
    "find_correlations",
    "load_alert_contexts",
    "persist_correlations",
    "build_and_persist_correlations_for_batch",
]
