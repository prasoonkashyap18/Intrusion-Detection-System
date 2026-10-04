"""Batch-processing lifecycle: the pending -> processing transition, and (as
of this step) CSV ingestion, dataset-schema recognition, feature mapping,
feature persistence, and model inference as the real processing boundary.

Lifecycle (conservative on purpose — no other transitions are allowed yet):

    pending -> processing -> completed
    pending -> processing -> failed

This module does not implement completed: nothing in this step persists a
detection result, so a batch whose CSV ingests, feature-extracts, and (when
applicable) runs inference cleanly is left `processing` rather than
`completed` — marking it `completed` would claim a finished detection
pipeline when the prediction it produced was never even recorded. **Running
inference is not the same as completing detection**: Step 26 produces an
in-memory `InferenceResult` and discards it once logged; nothing about that
result is persisted, so there is nothing yet for `completed` to refer to.
The existing status model has no state between "processing" and "completed"
(e.g. an "inferred" status), and this step deliberately does not invent
one; see "Processing boundary" below and backend/README.md for where a
later step is expected to make the `processing -> completed` transition
once it actually persists a detection result.

A batch reaches `failed` here if processing could not even start (its
upload file is missing), its CSV fails to ingest (missing file, unreadable,
or structurally malformed — see app.services.ingestion), an unexpected
error occurs during ingestion or feature extraction, or — new in this step —
inference could not run at all for a batch whose dataset schema was
recognized (no compatible `READY` model exists, or the model's artifact is
missing/corrupt/incompatible; see `_run_model_inference`). It does NOT fail
merely because an individual record is missing an optional value, has a
value that can't be parsed (`FeatureStatus.MALFORMED`), or uses an encoding
this layer doesn't resolve yet (`FeatureStatus.UNSUPPORTED`) — see
app.services.feature_extraction. Those are expected, per-record,
per-feature outcomes in heterogeneous network-flow data, not processing
failures; the batch keeps going and reports them (see `ProcessingSummary`),
it does not abort over them. `processed_records` and `failed_records` keep
their pre-this-step meaning (detection outcomes) and are therefore left at 0
here: rows ingested, feature-extracted, or passed through inference is not
the same thing as records detected, and nothing in this module claims
otherwise — inference predictions are not written into these counters.

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
parses cleanly, without claiming a dataset identity nobody confirmed. No
model inference is attempted in that case either — there is no persisted,
FEATURE_SCHEMA-ordered data to run it on.

Inference boundary (new in this step): `_run_model_inference`, called from
`start_processing` right after `_run_feature_extraction` succeeds, runs only
when the batch's dataset schema was recognized and at least one
`MappedFeatureRecord` row was persisted for it. It loads *this batch's own*
persisted feature vectors (via `app.services.feature_persistence.
load_feature_vector`, batch-isolated by querying on `batch_id` — another
batch's rows are never visible here), resolves a deterministic `READY`
model from the existing registry (`app.models.model_metadata.ModelMetadata`
— the most recently registered `READY` model whose `dataset_name` and
`feature_set_version` match this batch's), and calls the existing
`app.services.model_inference.predict_with_model` unchanged — this module
never re-implements artifact loading, preprocessing, compatibility
validation, or prediction; it only orchestrates. The resulting
`InferenceResult` is logged (model identity, sample count, a benign/attack
prediction count — real numbers from real predictions) and then discarded:
**it is not persisted**. No `DetectionResult` model or table exists yet;
that persistence, along with risk/severity/anomaly/confidence/alert
concepts, is explicitly deferred to a later step. If no compatible `READY`
model exists, or the model's artifact is missing, corrupt, or otherwise
unusable, this is treated as a processing failure (the batch becomes
`failed`, with a safe, generic message) rather than silently skipped —
inference was supposed to run and could not, which is different from the
"dataset not recognized" case above where there was never anything to run
it on.

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

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.detection_batch import DetectionBatch
from app.models.enums import ModelStatus, ProcessingStatus
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.model_metadata import ModelMetadata
from app.services.batch_service import get_batch
from app.services.dataset_adapters import select_adapter
from app.services.feature_extraction import FEATURE_SCHEMA_VERSION, extract_features
from app.services.feature_mapping import map_canonical_record
from app.services.feature_persistence import load_feature_vector, persist_mapped_features
from app.services.ingestion import ingest_batch, read_header
from app.services.model_inference import InferenceError, InferenceResult, predict_with_model

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
    never a guessed one (see the module docstring). Model inference (see
    `_run_model_inference`) is attempted only when `records_persisted > 0`;
    its result is not part of this dataclass — it is produced, logged, and
    discarded separately, since it describes a prediction outcome rather
    than an ingestion/feature-extraction outcome.
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
    invalid), that hits an unexpected error while ingesting, mapping, or
    persisting features, or for which model inference was attempted but
    could not run (no compatible `READY` model, or an unusable artifact —
    see `_run_model_inference`), is left in a `failed` state rather than
    raising — the caller gets back the batch's true resulting status
    instead of an error for a state the system handled. Any feature rows
    staged but not yet committed during a failed attempt are rolled back
    first, so a `failed` batch never leaves a partially persisted feature
    set behind.
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
        inference_result = _run_model_inference(db, batch_id, summary)
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
    if inference_result is not None:
        benign_count = sum(1 for p in inference_result.predictions if p.predicted_label == 0)
        attack_count = sum(1 for p in inference_result.predictions if p.predicted_label == 1)
        logger.info(
            "Batch %s: inference completed with model %s v%s — %d sample(s) (%d benign, %d attack); "
            "result is in-memory only and was not persisted",
            batch_id,
            inference_result.model_name,
            inference_result.model_version,
            inference_result.sample_count,
            benign_count,
            attack_count,
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
    dataset's features. No `DetectionResult` rows are created here either
    way; model inference (a separate step — see `_run_model_inference`) is
    only even attempted when a schema was recognized and rows were
    actually persisted.

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
            canonical = adapter.adapt(record)
            mapped = map_canonical_record(canonical)
            persist_mapped_features(
                db, batch_id, mapped, label=canonical.label, attack_category=canonical.attack_category
            )
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


def _run_model_inference(db: Session, batch_id: uuid.UUID, summary: ProcessingSummary) -> InferenceResult | None:
    """Runs the existing `app.services.model_inference.predict_with_model`
    service over *this batch's own* persisted feature vectors — orchestration
    only; artifact loading, preprocessing, compatibility validation, and
    prediction all remain that module's responsibility and are never
    reimplemented here.

    Returns `None` (skips inference) when this batch's dataset schema was
    not recognized or no features were persisted for it (`summary.
    records_persisted == 0`) — there is nothing `FEATURE_SCHEMA`-ordered to
    run inference on, and this is not a failure (see the module docstring's
    "Inference boundary" section on why this differs from the no-model case
    below).

    Raises `ApiException` (caught by `start_processing`'s existing error
    handling, same as any other processing failure) if no compatible
    `READY` model is found, or if `predict_with_model` itself raises
    `InferenceError` — both are processing failures once a schema has been
    recognized and inference was supposed to run. Messages are safe (no
    filesystem paths, no stack traces, no raw exception text); full detail
    is logged server-side only.
    """
    if summary.records_persisted == 0 or summary.dataset_schema is None:
        logger.info("Batch %s: no persisted features to run inference on; skipping inference", batch_id)
        return None

    feature_matrix = _load_feature_matrix_for_batch(db, batch_id)
    if not feature_matrix:
        logger.info("Batch %s: no persisted feature rows found; skipping inference", batch_id)
        return None

    model = _select_ready_model(db, summary.dataset_schema, summary.feature_schema_version)
    if model is None:
        logger.error(
            "Batch %s: no READY model compatible with dataset_schema=%s, feature_schema_version=%s",
            batch_id,
            summary.dataset_schema,
            summary.feature_schema_version,
        )
        raise ApiException(
            500,
            "inference_unavailable",
            "No compatible trained model is available to run inference for this batch.",
        )

    try:
        return predict_with_model(
            db,
            model.id,
            feature_matrix,
            feature_schema_version=summary.feature_schema_version,
            dataset_schema=summary.dataset_schema,
        )
    except InferenceError:
        logger.exception("Batch %s: model inference failed", batch_id)
        raise ApiException(500, "inference_unavailable", "Unable to run model inference for this batch.") from None


def _select_ready_model(db: Session, dataset_schema: str, feature_schema_version: str) -> ModelMetadata | None:
    """Deterministically selects one `READY` model compatible with
    `dataset_schema`/`feature_schema_version`: the most recently
    registered match (`created_at` descending), with ties broken by `id`
    descending so the choice never depends on query-plan or insertion-
    order happenstance. Does not fabricate a match, train one, or fall
    back to an incompatible model — `None` means "nothing suitable
    exists," left for the caller to handle."""
    return db.scalars(
        select(ModelMetadata)
        .where(
            ModelMetadata.status == ModelStatus.READY,
            ModelMetadata.dataset_name == dataset_schema,
            ModelMetadata.feature_set_version == feature_schema_version,
        )
        .order_by(ModelMetadata.created_at.desc(), ModelMetadata.id.desc())
    ).first()


def _load_feature_matrix_for_batch(db: Session, batch_id: uuid.UUID) -> list[list[float | None]]:
    """Loads *only* `batch_id`'s own persisted `MappedFeatureRecord` rows,
    in deterministic `row_number` order, as `FEATURE_SCHEMA`-ordered
    vectors via the existing `app.services.feature_persistence.
    load_feature_vector` — the same mechanism `app.services.training_data`
    uses, never a fresh reconstruction. Deliberately does not go through
    `training_data.load_training_data`: that loader excludes any row whose
    label can't be mapped to BENIGN/ATTACK, which is correct for building a
    *training* set but wrong here — real inference input is typically
    unlabeled entirely, and excluding unlabeled rows would make inference
    silently predict on nothing. The `batch_id` filter below is the entire
    batch-isolation guarantee: another batch's rows are never a match."""
    rows = db.scalars(
        select(MappedFeatureRecord)
        .where(MappedFeatureRecord.batch_id == batch_id)
        .order_by(MappedFeatureRecord.row_number)
    ).all()
    return [load_feature_vector(row) for row in rows]


def _mark_failed(db: Session, batch: DetectionBatch, message: str) -> None:
    batch.status = ProcessingStatus.FAILED
    batch.error_message = message
    batch.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(batch)
