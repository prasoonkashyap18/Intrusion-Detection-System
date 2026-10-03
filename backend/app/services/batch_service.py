"""Read access to persisted detection batches."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.detection_batch import DetectionBatch

logger = logging.getLogger("ai_ids")


@dataclass(frozen=True)
class BatchPage:
    items: list[DetectionBatch]
    total_items: int
    total_pages: int


def list_batches(db: Session, *, page: int, page_size: int) -> BatchPage:
    """Returns one page of batches, newest first.

    Ordering and paging happen in SQL (`ORDER BY created_at DESC LIMIT/OFFSET`);
    `id` breaks ties so pages never overlap or skip rows. Two queries total —
    the page and a COUNT — and no relationships are loaded.
    """
    try:
        total_items = db.scalar(select(func.count()).select_from(DetectionBatch)) or 0
        items = list(
            db.scalars(
                select(DetectionBatch)
                .order_by(DetectionBatch.created_at.desc(), DetectionBatch.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
    except SQLAlchemyError:
        # Details go to the log only; the client gets a generic message.
        logger.exception("Failed to load detection batches")
        raise ApiException(500, "batches_unavailable", "Unable to load detection batches.") from None

    return BatchPage(items=items, total_items=total_items, total_pages=math.ceil(total_items / page_size))
