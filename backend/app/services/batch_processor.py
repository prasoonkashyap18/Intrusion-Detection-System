"""Batch-processing lifecycle: the pending -> processing transition, and (as
of this step) CSV ingestion as the first real stage of processing.

Lifecycle (conservative on purpose — no other transitions are allowed yet):

    pending -> processing -> completed
    pending -> processing -> failed

This module does not implement completed: nothing in this step performs
detection, so a batch whose CSV ingests cleanly is left `processing` rather
than `completed` — marking it `completed` would claim IDS analysis happened
when only ingestion did. The existing status model has no state between the
two (e.g. an "ingested" status), and this step deliberately does not invent
one; see "Processing boundary" below and backend/README.md for where a later
step is expected to make the `processing -> completed` transition once it
actually produces detection results. A batch only reaches `failed` here if
processing could not even start (its upload file is missing), its CSV fails
to ingest (missing file, unreadable, or structurally malformed — see
app.services.ingestion), or an unexpected error occurs. `processed_records`
and `failed_records` keep their pre-this-step meaning (detection outcomes)
and are therefore left at 0 by ingestion: rows ingested is not the same
thing as records detected, and nothing here claims otherwise.

Processing boundary: `_ingest_batch_csv` below reads the batch's stored CSV
via `app.services.ingestion.ingest_batch` and validates every row, but
otherwise does nothing with the records it reads — no feature mapping, no
model inference, no `DetectionResult` rows. A later step is expected to
replace its body with that work.

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
from app.services.ingestion import ingest_batch

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
    """Claims a pending batch and runs the CSV-ingestion processing boundary.

    Raises ApiException: 404 if the batch does not exist, 409 if it is not
    pending (including the case where a concurrent call just claimed it),
    500 on a database failure while claiming it. A batch that is claimed but
    whose CSV cannot be ingested (missing file, unreadable, or structurally
    invalid), or that hits an unexpected error, is left in a `failed` state
    rather than raising — the caller gets back the batch's true resulting
    status instead of an error for a state the system handled.
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
    try:
        _ingest_batch_csv(csv_path)
    except ApiException as error:
        logger.error("CSV ingestion failed for batch %s: %s", batch_id, error.message)
        _mark_failed(db, batch, error.message)
        return batch
    except Exception:
        logger.exception("Unexpected error while starting processing for batch %s", batch_id)
        _mark_failed(db, batch, "An unexpected error occurred while starting processing.")
        return batch

    return batch


def _ingest_batch_csv(csv_path: Path) -> None:
    """The processing boundary: reads and validates the batch's CSV into the
    generic NetworkFlowRecord representation (app.services.ingestion). A
    later step will replace this body to consume each record for feature
    mapping and model inference; for now, fully exhausting the iterator
    proves every row reads and validates, and nothing else is done with it
    — no DetectionResult rows are created and no prediction is made.
    """
    for _record in ingest_batch(csv_path):
        pass


def _mark_failed(db: Session, batch: DetectionBatch, message: str) -> None:
    batch.status = ProcessingStatus.FAILED
    batch.error_message = message
    batch.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(batch)
