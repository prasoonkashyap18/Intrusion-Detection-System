"""Batch-processing lifecycle: the pending -> processing transition, and (as
of this step) CSV ingestion followed by feature extraction as the real
processing boundary.

Lifecycle (conservative on purpose — no other transitions are allowed yet):

    pending -> processing -> completed
    pending -> processing -> failed

This module does not implement completed: nothing in this step performs
detection, so a batch whose CSV ingests and feature-extracts cleanly is left
`processing` rather than `completed` — marking it `completed` would claim
IDS analysis happened when only data preparation did. **Feature extraction
is not detection**: it turns rows into a model-ready representation; it does
not classify, score, or decide anything about them. The existing status
model has no state between "processing" and "completed" (e.g. an
"extracted" status), and this step deliberately does not invent one; see
"Processing boundary" below and backend/README.md for where a later step is
expected to make the `processing -> completed` transition once it actually
produces detection results.

A batch reaches `failed` here if processing could not even start (its
upload file is missing), its CSV fails to ingest (missing file, unreadable,
or structurally malformed — see app.services.ingestion), or an unexpected
error occurs during ingestion or feature extraction. It does NOT fail merely
because an individual record is missing an optional value, has a value that
can't be parsed (`FeatureStatus.MALFORMED`), or uses an encoding this layer
doesn't resolve yet (`FeatureStatus.UNSUPPORTED`) — see app.services.
feature_extraction. Those are expected, per-record, per-feature outcomes in
heterogeneous network-flow data, not processing failures; the batch keeps
going and reports them (see `ProcessingSummary`), it does not abort over
them. `processed_records` and `failed_records` keep their pre-this-step
meaning (detection outcomes) and are therefore left at 0 here: rows ingested
or feature-extracted is not the same thing as records detected, and nothing
in this module claims otherwise.

Processing boundary: `_run_feature_extraction` below streams the batch's
stored CSV through `app.services.ingestion.ingest_batch`, feature-extracts
every `NetworkFlowRecord` it yields via `app.services.feature_extraction.
extract_features`, and otherwise does nothing with the result — no model
inference, no `DetectionResult` rows. The resulting `NormalizedFlowFeatures`
are not persisted or returned anywhere (see "Feature representation
persistence" in backend/README.md for why). A later step is expected to
replace this function's body with the work that actually consumes them.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.services.batch_service import get_batch
from app.services.feature_extraction import FEATURE_SCHEMA_VERSION, extract_features
from app.services.ingestion import ingest_batch

logger = logging.getLogger("ai_ids")


@dataclass(frozen=True)
class ProcessingSummary:
    """Internal, in-memory-only summary of one processing run's ingestion +
    feature-extraction stage. Never persisted and never returned through the
    API (see backend/README.md) — it exists only for the server-side log
    line `start_processing` writes on success.

    These counts describe data-quality outcomes of reading and preparing
    rows, never detection outcomes: a row that feature-extracts cleanly is
    not "safe", and one with MALFORMED or UNSUPPORTED field values is not
    "an attack" or "a failed detection" — see FeatureStatus in
    app.services.feature_extraction. This step does not track a separate
    "records that could not be represented" count, because
    `extract_features` never fails for an individual record's missing,
    malformed, or unsupported field values — only a structural ingestion
    failure or a genuinely unexpected error aborts the whole batch (see the
    module docstring), so under this design every record that is ingested
    is, by construction, also feature-extracted.
    """

    records_ingested: int
    records_feature_extracted: int
    feature_schema_version: str


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
    """Claims a pending batch and runs the ingestion + feature-extraction
    processing boundary.

    Raises ApiException: 404 if the batch does not exist, 409 if it is not
    pending (including the case where a concurrent call just claimed it),
    500 on a database failure while claiming it. A batch that is claimed but
    whose CSV cannot be ingested (missing file, unreadable, or structurally
    invalid), or that hits an unexpected error while ingesting or
    feature-extracting, is left in a `failed` state rather than raising —
    the caller gets back the batch's true resulting status instead of an
    error for a state the system handled.
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
        summary = _run_feature_extraction(csv_path)
    except ApiException as error:
        logger.error("Processing failed for batch %s: %s", batch_id, error.message)
        _mark_failed(db, batch, error.message)
        return batch
    except Exception:
        logger.exception("Unexpected error while starting processing for batch %s", batch_id)
        _mark_failed(db, batch, "An unexpected error occurred while starting processing.")
        return batch

    logger.info(
        "Batch %s: %d record(s) ingested, %d feature-extracted (schema v%s)",
        batch_id,
        summary.records_ingested,
        summary.records_feature_extracted,
        summary.feature_schema_version,
    )
    return batch


def _run_feature_extraction(csv_path: Path) -> ProcessingSummary:
    """The processing boundary: streams the batch's CSV through ingestion
    (app.services.ingestion), generic-feature-extracts every record via
    app.services.feature_extraction, and otherwise does nothing with the
    result — no DetectionResult rows are created and no prediction is made.
    A later step will replace this body to consume each NormalizedFlowFeatures
    for model inference.

    Streaming: `ingest_batch` is a generator; each `NetworkFlowRecord` is
    feature-extracted and discarded before the next one is read, so at most
    one record and one feature set are held in memory at a time — this
    function never materializes the batch's rows as a list.
    """
    records_ingested = 0
    records_feature_extracted = 0
    for record in ingest_batch(csv_path):
        records_ingested += 1
        extract_features(record)
        records_feature_extracted += 1

    return ProcessingSummary(
        records_ingested=records_ingested,
        records_feature_extracted=records_feature_extracted,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
    )


def _mark_failed(db: Session, batch: DetectionBatch, message: str) -> None:
    batch.status = ProcessingStatus.FAILED
    batch.error_message = message
    batch.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(batch)
