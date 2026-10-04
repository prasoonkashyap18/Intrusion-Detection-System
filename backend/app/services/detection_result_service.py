"""Read access to persisted `DetectionResult` rows — the query layer
Step 28's API endpoints sit on top of.

    Database (DetectionResult rows, written by Step 27)
        |
    list_detection_results_for_batch() / get_detection_result()
        |
    app.api.v1.detection (FastAPI router)

This module performs NO analysis: it never calls model inference, never
reads a batch's CSV, never runs feature extraction, and never writes to
`DetectionResult` — purely a read path over rows some earlier processing
run already persisted (see `app.services.detection_result_persistence`).
Mirrors the existing `app.services.batch_service` conventions (same
`ApiException` usage, same `{page, page_size, total_items, total_pages}`
paging shape) rather than inventing a new pattern.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ApiException
from app.models.detection_result import DetectionResult

logger = logging.getLogger("ai_ids")


@dataclass(frozen=True)
class DetectionResultPage:
    items: list[DetectionResult]
    total_items: int
    total_pages: int


def list_detection_results_for_batch(
    db: Session, batch_id: uuid.UUID, *, page: int, page_size: int
) -> DetectionResultPage:
    """Returns one page of `batch_id`'s own `DetectionResult` rows, ordered
    by `row_number` ascending (ties — which should never occur given the
    `UniqueConstraint("batch_id", "row_number")` — broken by `id` ascending
    for a fully deterministic order regardless).

    The `batch_id` filter is the entire batch-isolation guarantee: this
    query can never return another batch's rows. Ordering and paging
    happen in SQL (`ORDER BY ... LIMIT/OFFSET`), and the related model's
    name/version are eagerly loaded (`selectinload`) in the same query
    batch rather than once per row, so a full page never costs more than
    two round trips total (the page, and a COUNT) regardless of page size.
    Does not validate that `batch_id` actually exists — see
    `app.services.batch_service.get_batch` for that, called by the router
    before this.
    """
    try:
        total_items = (
            db.scalar(
                select(func.count()).select_from(DetectionResult).where(DetectionResult.batch_id == batch_id)
            )
            or 0
        )
        items = list(
            db.scalars(
                select(DetectionResult)
                .where(DetectionResult.batch_id == batch_id)
                .options(selectinload(DetectionResult.model))
                .order_by(DetectionResult.row_number.asc(), DetectionResult.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
    except SQLAlchemyError:
        logger.exception("Failed to load detection results for batch %s", batch_id)
        raise ApiException(
            500, "detection_results_unavailable", "Unable to load detection results for this batch."
        ) from None

    return DetectionResultPage(items=items, total_items=total_items, total_pages=math.ceil(total_items / page_size))


def get_detection_result(db: Session, result_id: uuid.UUID) -> DetectionResult:
    """Returns one `DetectionResult` by id, or raises `ApiException` (404
    if it does not exist, 500 on a database failure). Never mutates the
    row it returns."""
    try:
        result = db.scalars(
            select(DetectionResult)
            .where(DetectionResult.id == result_id)
            .options(selectinload(DetectionResult.model))
        ).first()
    except SQLAlchemyError:
        logger.exception("Failed to load detection result %s", result_id)
        raise ApiException(500, "detection_result_unavailable", "Unable to load the detection result.") from None

    if result is None:
        raise ApiException(404, "detection_result_not_found", "No detection result was found with that ID.")
    return result


__all__ = ["DetectionResultPage", "list_detection_results_for_batch", "get_detection_result"]
