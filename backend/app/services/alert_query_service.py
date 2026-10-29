"""Read access to persisted `Alert` rows — the query layer the Step 37
API endpoints sit on top of. Mirrors `app.services.
detection_result_service`'s own conventions (same `ApiException` usage,
same `{page, page_size, total_items, total_pages}` paging shape) rather
than inventing a new pattern.

This module performs NO analysis and never writes to `Alert` — see
`app.services.alert_lifecycle.transition_alert` for the only place an
alert's status may change.
"""

from __future__ import annotations

import math
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.alert import Alert
from app.models.enums import AlertStatus, Severity

logger = logging.getLogger("ai_ids")

_SORTABLE_FIELDS: dict[str, object] = {
    "created_at": Alert.created_at,
    "updated_at": Alert.updated_at,
    "severity": Alert.severity,
}


@dataclass(frozen=True)
class AlertPage:
    items: list[Alert]
    total_items: int
    total_pages: int


def list_alerts(
    db: Session,
    *,
    page: int,
    page_size: int,
    status: AlertStatus | None = None,
    severity: Severity | None = None,
    batch_id: uuid.UUID | None = None,
    sort_by: str = "created_at",
    descending: bool = True,
) -> AlertPage:
    """Returns one page of persisted `Alert` rows, optionally filtered
    by `status`/`severity`/`batch_id` (each applied only when supplied
    — omitting all three returns every alert) and sorted by one of
    `_SORTABLE_FIELDS`, with ties broken by `id` for a fully
    deterministic order regardless of the primary sort field.

    Raises `ApiException` 422 for an unsupported `sort_by` value — the
    same "reject rather than silently fall back" convention the rest of
    this API already follows for invalid query parameters.
    """
    if sort_by not in _SORTABLE_FIELDS:
        raise ApiException(
            422, "invalid_sort_field", f"sort_by must be one of: {', '.join(sorted(_SORTABLE_FIELDS))}."
        )

    conditions = []
    if status is not None:
        conditions.append(Alert.status == status)
    if severity is not None:
        conditions.append(Alert.severity == severity)
    if batch_id is not None:
        conditions.append(Alert.batch_id == batch_id)

    sort_column = _SORTABLE_FIELDS[sort_by]
    order = sort_column.desc() if descending else sort_column.asc()
    id_tiebreak = Alert.id.desc() if descending else Alert.id.asc()

    try:
        total_items = db.scalar(select(func.count()).select_from(Alert).where(*conditions)) or 0
        items = list(
            db.scalars(
                select(Alert)
                .where(*conditions)
                .order_by(order, id_tiebreak)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
    except SQLAlchemyError:
        logger.exception("Failed to load alerts")
        raise ApiException(500, "alerts_unavailable", "Unable to load alerts.") from None

    return AlertPage(items=items, total_items=total_items, total_pages=math.ceil(total_items / page_size) if total_items else 0)


def get_alert_or_404(db: Session, alert_id: uuid.UUID) -> Alert:
    """Returns one `Alert` by id, or raises `ApiException` (404 if it
    does not exist, 500 on a database failure)."""
    try:
        alert = db.get(Alert, alert_id)
    except SQLAlchemyError:
        logger.exception("Failed to load alert %s", alert_id)
        raise ApiException(500, "alert_unavailable", "Unable to load the alert.") from None

    if alert is None:
        raise ApiException(404, "alert_not_found", "No alert was found with that ID.")
    return alert


__all__ = ["AlertPage", "list_alerts", "get_alert_or_404"]
