"""Batch-processing lifecycle: the pending -> processing transition, and (as
of this step) CSV ingestion, dataset-schema recognition, feature mapping and
feature persistence as the real processing boundary.

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

Processing boundary: `_run_feature_extraction` below reads the batch's CSV
header once and tries `app.services.dataset_adapters.select_adapter` on it.
If a dataset adapter recognizes the header, every `NetworkFlowRecord`
`app.services.ingestion.ingest_batch` yields is adapted
(`DatasetAdapter.adapt`), mapped (`app.services.feature_mapping.
map_canonical_record`) and persisted (`app.services.feature_persistence.
persist_mapped_features`) as a `MappedFeatureRecord` row. If no adapter
recognizes the header (`UNSUPPORTED`/`AMBIGUOUS` — see `dataset_adapters.
registry.AdapterSelection`), this module does not guess: no row is
adapted, mapped or persisted for that batch, and each record is instead
passed through the plain `app.services.feature_extraction.extract_features`
path Step 17 already established — proving every row still reads and
parses cleanly, without claiming a dataset identity nobody confirmed.
Either way, no model inference runs and no `DetectionResult` row is
created. A later step is expected to replace the persisted-feature path
with the work that actually consumes `MappedFeatureRecord` rows (model
inference), and is responsible for however a batch eventually declares or
selects its dataset explicitly, rather than this module inferring one.

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
from app.services.dataset_adapters import select_adapter
from app.services.feature_extraction import FEATURE_SCHEMA_VERSION, extract_features
from app.services.feature_mapping import map_canonical_record
from app.services.feature_persistence import persist_mapped_features
from app.services.ingestion import ingest_batch, read_header

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
    `extract_features`/`map_canonical_record` never fail for an individual
    record's missing, malformed, or unsupported field values — only a
    structural ingestion failure or a genuinely unexpected error aborts the
    whole batch (see the module docstring), so under this design every
    record that is ingested is, by construction, also feature-extracted.

    `records_persisted` is 0 whenever no dataset adapter recognized the
    batch's CSV header — persistence only happens for a recognized schema,
    never a guessed one (see the module docstring).
    """

    records_ingested: int
    records_feature_extracted: int
    records_persisted: int
    feature_schema_version: str
    dataset_schema: str | None
    """The matched adapter's `schema_id`, or `None` if no adapter
    recognized this batch's CSV header."""


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
    """Claims a pending batch and runs the ingestion + feature-mapping +
    feature-persistence processing boundary.

    Raises ApiException: 404 if the batch does not exist, 409 if it is not
    pending (including the case where a concurrent call just claimed it),
    500 on a database failure while claiming it. A batch that is claimed but
    whose CSV cannot be ingested (missing file, unreadable, or structurally
    invalid), or that hits an unexpected error while ingesting, mapping, or
    persisting features, is left in a `failed` state rather than raising —
    the caller gets back the batch's true resulting status instead of an
    error for a state the system handled. Any feature rows staged but not
    yet committed during a failed attempt are rolled back first, so a
    `failed` batch never leaves a partially persisted feature set behind.
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
        summary = _run_feature_extraction(db, batch_id, csv_path)
    except ApiException as error:
        db.rollback()  # discard any feature rows staged but not committed
        logger.error("Processing failed for batch %s: %s", batch_id, error.message)
        _mark_failed(db, batch, error.message)
        return batch
    except Exception:
        db.rollback()  # discard any feature rows staged but not committed
        logger.exception("Unexpected error while starting processing for batch %s", batch_id)
        _mark_failed(db, batch, "An unexpected error occurred while starting processing.")
        return batch

    logger.info(
        "Batch %s: %d record(s) ingested, %d feature-extracted, %d persisted (schema v%s, dataset=%s)",
        batch_id,
        summary.records_ingested,
        summary.records_feature_extracted,
        summary.records_persisted,
        summary.feature_schema_version,
        summary.dataset_schema or "unrecognized",
    )
    return batch


def _run_feature_extraction(db: Session, batch_id: uuid.UUID, csv_path: Path) -> ProcessingSummary:
    """The processing boundary: reads the batch's CSV header once and tries
    to recognize its dataset schema (`app.services.dataset_adapters.
    select_adapter`). For a recognized schema, every `NetworkFlowRecord`
    `ingest_batch` yields is adapted, mapped, and staged for persistence as
    a `MappedFeatureRecord` (not committed here — see `start_processing`
    and `app.services.feature_persistence`'s module docstring for the
    transaction boundary). For an unrecognized schema, records are instead
    passed through the plain `feature_extraction.extract_features` path,
    proving they read and parse cleanly without persisting a guessed
    dataset's features. No `DetectionResult` rows are created and no
    prediction is made either way.

    Streaming: `ingest_batch` is a generator; each `NetworkFlowRecord` is
    processed and discarded before the next one is read, so at most one
    record and one feature set are held in memory at a time — this
    function never materializes the batch's rows as a list.
    """
    header = read_header(csv_path)
    adapter = select_adapter(header).adapter

    records_ingested = 0
    records_feature_extracted = 0
    records_persisted = 0

    for record in ingest_batch(csv_path):
        records_ingested += 1
        if adapter is not None:
            mapped = map_canonical_record(adapter.adapt(record))
            persist_mapped_features(db, batch_id, mapped)
            records_persisted += 1
        else:
            extract_features(record)
        records_feature_extracted += 1

    if records_persisted:
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Failed to persist mapped features for batch %s", batch_id)
            raise ApiException(
                500, "feature_persistence_unavailable", "Unable to persist mapped features for this batch."
            ) from None

    return ProcessingSummary(
        records_ingested=records_ingested,
        records_feature_extracted=records_feature_extracted,
        records_persisted=records_persisted,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        dataset_schema=adapter.schema_id if adapter is not None else None,
    )


def _mark_failed(db: Session, batch: DetectionBatch, message: str) -> None:
    batch.status = ProcessingStatus.FAILED
    batch.error_message = message
    batch.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(batch)
