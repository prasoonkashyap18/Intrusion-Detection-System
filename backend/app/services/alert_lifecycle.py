"""Alert lifecycle: centralized, validated `AlertStatus` transitions for
a persisted `Alert`, with an append-only audit trail
(`AlertStatusHistory`).

    Alert.status (current)
        |
    transition_alert()  <- the ONLY place that may change Alert.status
        |
    AlertStatusHistory row (previous_status, new_status, note, changed_at)
        |
    Alert.status (new) + Alert.updated_at

No other module writes to `Alert.status`: `app.services.
attack_chain_analysis` and `app.services.event_correlation` are
strictly read-only with respect to alerts (see their own module
docstrings), and nothing in `app.api.v1.alerts` touches `Alert.status`
directly — every status change goes through `transition_alert`, so the
valid-transition rules below are enforced exactly once, in exactly one
place, never duplicated at the API layer or left to a caller's
discipline.

Valid transitions (`VALID_TRANSITIONS`):

    OPEN           -> ACKNOWLEDGED, INVESTIGATING, RESOLVED, FALSE_POSITIVE
    ACKNOWLEDGED   -> INVESTIGATING, RESOLVED, FALSE_POSITIVE
    INVESTIGATING  -> ACKNOWLEDGED, RESOLVED, FALSE_POSITIVE
    RESOLVED       -> (terminal — no further transition)
    FALSE_POSITIVE -> (terminal — no further transition)

A request whose target status equals the alert's current status is
treated as an idempotent no-op (returns the alert unchanged, writes no
new history row) rather than an error — a retried identical request
must behave predictably, not fail the second time just because the
first one already succeeded. Any other transition not listed above
(including every transition out of a terminal state) raises
`InvalidAlertTransitionError`.

Nothing here ever touches `Alert.evidence`, or any of its FKs to
`DetectionResult`/`AnomalyResult`/`HybridDetectionResult`/
`RiskAssessment`/`DetectionExplanation` — the original detection
evidence and risk assessment are preserved exactly as `app.services.
alert_generation` wrote them, regardless of how many times an alert's
status changes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.alert import Alert
from app.models.alert_status_history import AlertStatusHistory
from app.models.enums import AlertStatus
from app.services.alert_query_service import get_alert_or_404

logger = logging.getLogger("ai_ids")

VALID_TRANSITIONS: dict[AlertStatus, frozenset[AlertStatus]] = {
    AlertStatus.OPEN: frozenset(
        {AlertStatus.ACKNOWLEDGED, AlertStatus.INVESTIGATING, AlertStatus.RESOLVED, AlertStatus.FALSE_POSITIVE}
    ),
    AlertStatus.ACKNOWLEDGED: frozenset(
        {AlertStatus.INVESTIGATING, AlertStatus.RESOLVED, AlertStatus.FALSE_POSITIVE}
    ),
    AlertStatus.INVESTIGATING: frozenset(
        {AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED, AlertStatus.FALSE_POSITIVE}
    ),
    AlertStatus.RESOLVED: frozenset(),
    AlertStatus.FALSE_POSITIVE: frozenset(),
}


class InvalidAlertTransitionError(Exception):
    """Raised for any `(current_status, requested_status)` pair not in
    `VALID_TRANSITIONS` (and not a same-status no-op). `message` is
    safe to surface directly to an API caller."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def is_valid_transition(current: AlertStatus, requested: AlertStatus) -> bool:
    """Pure function: `True` for a same-status no-op or a listed
    transition, `False` otherwise."""
    return requested == current or requested in VALID_TRANSITIONS[current]


def transition_alert(
    db: Session,
    alert_id: uuid.UUID,
    requested_status: AlertStatus,
    *,
    note: str | None = None,
) -> Alert:
    """Validates and applies one status transition for `alert_id`,
    writing an `AlertStatusHistory` row alongside it in the same
    transaction. Raises `ApiException` (404 if the alert does not
    exist) or `InvalidAlertTransitionError` (for a transition not in
    `VALID_TRANSITIONS`) *before* making any change — an invalid
    request never partially applies. A same-status request is a no-op:
    the alert is returned unchanged, with no new history row and no
    database write at all.

    Raises `ApiException` (500) if the commit itself fails; the
    session is rolled back first, so a failed attempt never leaves the
    alert or its history in a partially updated state.
    """
    alert = get_alert_or_404(db, alert_id)
    current_status = alert.status

    if requested_status == current_status:
        return alert

    if not is_valid_transition(current_status, requested_status):
        raise InvalidAlertTransitionError(
            f"Cannot transition an alert from '{current_status.value}' to '{requested_status.value}'."
        )

    history_row = AlertStatusHistory(
        alert_id=alert.id,
        previous_status=current_status,
        new_status=requested_status,
        note=note,
    )
    alert.status = requested_status
    alert.updated_at = datetime.now(timezone.utc)

    try:
        db.add(history_row)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist alert status transition for %s", alert_id)
        raise ApiException(500, "alert_transition_unavailable", "Unable to update the alert's status.") from None

    db.refresh(alert)
    return alert


def list_status_history(db: Session, alert_id: uuid.UUID) -> list[AlertStatusHistory]:
    """Returns `alert_id`'s full status-change audit trail, ordered by
    `changed_at` ascending — the same ordering `Alert.status_history`'s
    relationship already uses. Raises `ApiException` 404 if the alert
    itself does not exist."""
    get_alert_or_404(db, alert_id)
    return list(
        db.query(AlertStatusHistory)
        .filter(AlertStatusHistory.alert_id == alert_id)
        .order_by(AlertStatusHistory.changed_at.asc())
        .all()
    )


__all__ = [
    "VALID_TRANSITIONS",
    "InvalidAlertTransitionError",
    "is_valid_transition",
    "get_alert_or_404",
    "transition_alert",
    "list_status_history",
]
