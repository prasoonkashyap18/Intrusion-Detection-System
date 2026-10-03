"""Batch-processing lifecycle: the pending -> processing transition, and the
placeholder processing boundary that a later step's CSV parsing, feature
extraction and ML inference will plug into.

Lifecycle (conservative on purpose — no other transitions are allowed yet):

    pending -> processing -> completed
    pending -> processing -> failed

This module does not implement completed: nothing in this step actually
analyzes traffic, so a batch that is successfully claimed and whose upload
file is present is left `processing` rather than `completed` — marking it
`completed` would falsely claim analysis happened. A batch only reaches
`failed` here if processing could not even start (its upload file is
missing) or an unexpected error occurs while starting it. Completing a batch
is left to the step that actually runs the detection pipeline.

Concurrency: the pending -> processing transition is one atomic
`UPDATE ... WHERE status = 'pending'` statement (`claim_for_processing`
below), committed immediately. Two callers that both observed `pending`
cannot both win: whichever UPDATE's WHERE clause is evaluated first (SQLite
serializes writes to a database file) flips the row to `processing` and
commits, so the second UPDATE's WHERE clause no longer matches and it
affects zero rows. Checking `result.rowcount` after the statement — not a
separate SELECT beforehand — is what makes the claim race-free.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.services.batch_service import get_batch

logger = logging.getLogger("ai_ids")


def claim_for_processing(db: Session, batch_id: uuid.UUID) -> bool:
    """Atomically flips one batch from `pending` to `processing`.

    Returns True if this call made the transition, False if the batch was
    not pending (already claimed by another call, or in some other state).
    """
    result = db.execute(
        update(DetectionBatch)
        .where(DetectionBatch.id == batch_id, DetectionBatch.status == ProcessingStatus.PENDING)
        .values(status=ProcessingStatus.PROCESSING)
    )
    db.commit()
    return result.rowcount == 1


def start_processing(db: Session, batch_id: uuid.UUID, upload_dir: Path) -> DetectionBatch:
    """Claims a pending batch and runs the placeholder processing boundary.

    Raises ApiException: 404 if the batch does not exist, 409 if it is not
    pending (including the case where a concurrent call just claimed it),
    500 on a database failure. A batch that is claimed but whose upload file
    is missing, or that hits an unexpected error, is left in a `failed`
    state rather than raising — the caller gets back the batch's true
    resulting status instead of an error for a state the system handled.
    """
    batch = get_batch(db, batch_id)

    if batch.status != ProcessingStatus.PENDING:
        raise ApiException(
            409,
            "invalid_batch_state",
            f"Batch is '{batch.status.value}' and cannot be started; only a pending batch can be processed.",
        )

    try:
        claimed = claim_for_processing(db, batch_id)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to claim batch %s for processing", batch_id)
        raise ApiException(500, "processing_unavailable", "Unable to start processing for this batch.") from None

    if not claimed:
        raise ApiException(
            409,
            "invalid_batch_state",
            "Batch is no longer pending; it may already be processing.",
        )

    db.refresh(batch)

    csv_path = upload_dir / f"{batch_id}.csv"
    if not csv_path.is_file():
        logger.error("Upload file missing for batch %s", batch_id)
        _mark_failed(db, batch, "The uploaded file for this batch could not be found.")
        return batch

    try:
        _run_placeholder_processing(batch)
    except Exception:
        logger.exception("Unexpected error while starting processing for batch %s", batch_id)
        _mark_failed(db, batch, "An unexpected error occurred while starting processing.")
        return batch

    return batch


def _run_placeholder_processing(batch: DetectionBatch) -> None:
    """The processing boundary a future step will replace.

    A later step will read the batch's CSV here, extract features, run
    model inference, write DetectionResult rows, and update
    processed_records/failed_records as real rows are analyzed. For now
    this is intentionally a no-op: no rows are read and no predictions are
    made, so processed_records and failed_records are left untouched (0) —
    reporting any other value here would fabricate analysis that never ran.
    """
    return None


def _mark_failed(db: Session, batch: DetectionBatch, message: str) -> None:
    batch.status = ProcessingStatus.FAILED
    batch.error_message = message
    batch.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(batch)
