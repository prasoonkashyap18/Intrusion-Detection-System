"""Batch-processing lifecycle: the pending -> processing transition, and (as
of this step) CSV ingestion, dataset-schema recognition, feature mapping,
feature persistence, model inference, and detection-result persistence as
the real processing boundary.

Lifecycle (conservative on purpose — no other transitions are allowed yet):

    pending -> processing -> completed
    pending -> processing -> failed

This module does not implement completed: a batch whose CSV ingests,
feature-extracts, and (when applicable) runs inference and persists its
results cleanly is left `processing` rather than `completed`. Persisting a
`DetectionResult` row per prediction (Step 27) is not the same thing as the
system having decided a batch is "done" — there is still no risk/severity/
alert concept, no review workflow, and no later step has yet defined what
`completed` should mean for this pipeline. The existing status model has no
state between "processing" and "completed" (e.g. a "detected" status), and
this step deliberately does not invent one; see "Processing boundary" below
and backend/README.md for where a later step is expected to make the
`processing -> completed` transition.

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

Inference boundary: `_run_model_inference`, called from `start_processing`
right after `_run_feature_extraction` succeeds, runs only when the batch's
dataset schema was recognized and at least one `MappedFeatureRecord` row
was persisted for it. It loads *this batch's own* persisted feature
vectors and their `row_number`s (via `app.services.feature_persistence.
load_feature_vector`, batch-isolated by querying on `batch_id` — another
batch's rows are never visible here), resolves a deterministic `READY`
model from the existing registry (`app.models.model_metadata.ModelMetadata`
— the most recently registered `READY` model whose `dataset_name` and
`feature_set_version` match this batch's), and calls the existing
`app.services.model_inference.predict_with_model` unchanged — this module
never re-implements artifact loading, preprocessing, compatibility
validation, or prediction; it only orchestrates. If no compatible `READY`
model exists, or the model's artifact is missing, corrupt, or otherwise
unusable, this is treated as a processing failure (the batch becomes
`failed`, with a safe, generic message) rather than silently skipped —
inference was supposed to run and could not, which is different from the
"dataset not recognized" case above where there was never anything to run
it on.

Detection-result persistence boundary: once inference succeeds,
`_run_model_inference` passes the resulting `InferenceResult` and the
same `row_number`s to `app.services.detection_result_persistence.
persist_detection_results`, which writes one `DetectionResult` row per
prediction, each linked to this batch and the model that produced it —
never re-running inference, never duplicating the feature vector (already
on `MappedFeatureRecord`, reachable by `batch_id` + `row_number`). A
persistence failure is likewise a processing failure (`failed`, safe
message), handled the same way as an inference failure. `InferenceResult`
is still only logged beyond that — nothing about *risk, severity,
confidence scoring, alerting*, or any detection-workflow concept is added
by writing these rows.

Anomaly-detection boundary (Step 31, persistence added in Step 32):
`_run_anomaly_detection`, called from `start_processing` right after the
supervised inference block, is a second, entirely independent analysis
pass using the same batch-isolated feature vectors but a completely
separate `IsolationForest` model family (`app.services.
anomaly_detection_training`/`app.services.anomaly_inference`,
`model_type="isolation_forest"` in the same `ModelMetadata` registry
table — never confused with the supervised `"random_forest"` rows; see
`SUPERVISED_MODEL_TYPE`/`ANOMALY_MODEL_TYPE` below). Unlike supervised
inference, this pass is deliberately best-effort and never fails the
batch: no compatible `READY` anomaly detector, any failure during
anomaly detection itself, or (new in Step 32) any failure persisting its
result is caught, logged, and skipped, so a batch that would have
succeeded before Step 31 existed keeps succeeding identically. As of
Step 32, a successful result is persisted as one `AnomalyResult` row per
prediction (`app.services.anomaly_result_persistence.
persist_anomaly_results`), linked to this batch and the anomaly model
that produced it — kept in a table separate from `DetectionResult` (see
`app.models.anomaly_result`'s docstring for why) and never exposed
through any API or combined with supervised predictions in this step;
see backend/README.md's explicit Step 32 scope.

Derived-analysis boundary (Steps 33-35): `_run_hybrid_risk_and_explanations`,
called from `start_processing` right after the anomaly-detection block, is a
third analysis pass that reads back whatever `DetectionResult`/
`AnomalyResult` rows this batch just produced (never re-running inference or
anomaly detection itself) and combines them into `HybridDetectionResult`
(`app.services.hybrid_detection`), `RiskAssessment`
(`app.services.risk_scoring`), and `DetectionExplanation`
(`app.services.explainability`) rows — one triple per traffic row. Like
anomaly detection, it is entirely best-effort: it requires supervised
inference to have run (skipped, not failed, otherwise) but does not require
an anomaly result to exist for any given row, and any failure anywhere in
this layer is caught, logged, and swallowed rather than failing the batch.
These three new tables never alter `DetectionResult` or `AnomalyResult`
themselves — the original predictions, probabilities, flags, and scores stay
exactly as Steps 27/32 persisted them.

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
from app.services.anomaly_detection_training import MODEL_TYPE as ANOMALY_MODEL_TYPE
from app.services.anomaly_inference import AnomalyInferenceError, AnomalyInferenceResult, predict_anomalies_with_model
from app.services.anomaly_result_persistence import AnomalyResultPersistenceError, persist_anomaly_results
from app.services.batch_service import get_batch
from app.services.dataset_adapters import select_adapter
from app.services.detection_result_persistence import DetectionResultPersistenceError, persist_detection_results
from app.services.explainability import build_and_persist_explanations
from app.services.feature_extraction import FEATURE_SCHEMA_VERSION, extract_features
from app.services.feature_mapping import map_canonical_record
from app.services.feature_persistence import load_feature_vector, persist_mapped_features
from app.services.hybrid_detection import build_and_persist_hybrid_results
from app.services.ingestion import ingest_batch, read_header
from app.services.model_inference import InferenceError, InferenceResult, predict_with_model
from app.services.risk_scoring import build_and_persist_risk_assessments

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
    never a guessed one (see the module docstring). Model inference and
    detection-result persistence (see `_run_model_inference`) are
    attempted only when `records_persisted > 0`; neither is part of this
    dataclass — they describe a prediction outcome, not an ingestion/
    feature-extraction outcome.
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
            "%d detection result(s) persisted",
            batch_id,
            inference_result.model_name,
            inference_result.model_version,
            inference_result.sample_count,
            benign_count,
            attack_count,
            inference_result.sample_count,
        )

    # Step 31/32: an independent, best-effort analysis layer — never
    # affects the batch's resulting status, even when persisting its
    # result fails (see _run_anomaly_detection's docstring).
    anomaly_result = _run_anomaly_detection(db, batch_id, summary)
    if anomaly_result is not None:
        anomaly_count = sum(1 for p in anomaly_result.predictions if p.is_anomaly)
        logger.info(
            "Batch %s: anomaly detection completed with model %s v%s — %d sample(s), %d flagged anomalous; "
            "%d anomaly result(s) persisted",
            batch_id,
            anomaly_result.model_name,
            anomaly_result.model_version,
            anomaly_result.sample_count,
            anomaly_count,
            anomaly_result.sample_count,
        )

    # Steps 33-35: hybrid detection, risk scoring, and explainability —
    # an independent, best-effort derived-analysis layer over whatever
    # supervised/anomaly results were just persisted. Never affects the
    # batch's resulting status (see _run_hybrid_risk_and_explanations).
    hybrid_count = _run_hybrid_risk_and_explanations(db, batch_id, inference_result)
    if hybrid_count:
        logger.info(
            "Batch %s: hybrid detection, risk scoring, and explanations computed for %d row(s)",
            batch_id,
            hybrid_count,
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
    `READY` model is found, if `predict_with_model` itself raises
    `InferenceError`, or if persisting the resulting predictions as
    `DetectionResult` rows fails (`DetectionResultPersistenceError`) —
    all are processing failures once a schema has been recognized and
    inference was supposed to run. Messages are safe (no filesystem
    paths, no stack traces, no raw exception text); full detail is
    logged server-side only.
    """
    if summary.records_persisted == 0 or summary.dataset_schema is None:
        logger.info("Batch %s: no persisted features to run inference on; skipping inference", batch_id)
        return None

    feature_matrix, row_numbers = _load_feature_matrix_for_batch(db, batch_id)
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
        inference_result = predict_with_model(
            db,
            model.id,
            feature_matrix,
            feature_schema_version=summary.feature_schema_version,
            dataset_schema=summary.dataset_schema,
        )
    except InferenceError:
        logger.exception("Batch %s: model inference failed", batch_id)
        raise ApiException(500, "inference_unavailable", "Unable to run model inference for this batch.") from None

    try:
        persist_detection_results(db, batch_id, inference_result, row_numbers)
    except DetectionResultPersistenceError:
        logger.exception("Batch %s: failed to persist detection results", batch_id)
        raise ApiException(
            500, "detection_result_persistence_unavailable", "Unable to persist detection results for this batch."
        ) from None

    return inference_result


def _run_anomaly_detection(db: Session, batch_id: uuid.UUID, summary: ProcessingSummary) -> AnomalyInferenceResult | None:
    """Runs the existing `app.services.anomaly_inference.
    predict_anomalies_with_model` service over *this batch's own*
    persisted feature vectors — the same orchestration-only pattern
    `_run_model_inference` follows, over a completely independent model
    family (see `app.services.anomaly_detection_training`'s module
    docstring).

    Deliberately best-effort and non-fatal, unlike `_run_model_inference`:
    anomaly detection is an additional, independent analysis layer Step 31
    introduces, not a requirement the supervised pipeline already promised.
    A batch whose feature extraction and supervised inference succeeded
    must keep succeeding exactly as it did before Step 31 existed, even
    when no compatible anomaly detector has been trained yet, or anomaly
    detection itself fails unexpectedly — changing that would be exactly
    the "change existing classification behavior" this step must not do.
    Every failure path here is caught, logged server-side, and returns
    `None`; nothing propagates to `start_processing`.

    Returns `None` when there is nothing to run anomaly detection on (no
    recognized schema, no persisted features), when no compatible `READY`
    anomaly detector exists, when anomaly detection itself raises
    `AnomalyInferenceError` or any other unexpected exception, or (new in
    Step 32) when persisting its result raises
    `AnomalyResultPersistenceError` — every one of these is caught here
    and never propagates to `start_processing`. A successful result is
    persisted as one `AnomalyResult` row per prediction before being
    returned; see `app.services.anomaly_result_persistence.
    persist_anomaly_results` and backend/README.md's Step 32 scope.
    """
    if summary.records_persisted == 0 or summary.dataset_schema is None:
        return None

    try:
        feature_matrix, row_numbers = _load_feature_matrix_for_batch(db, batch_id)
        if not feature_matrix:
            return None

        model = _select_ready_anomaly_model(db, summary.dataset_schema, summary.feature_schema_version)
        if model is None:
            logger.info(
                "Batch %s: no READY anomaly detector compatible with dataset_schema=%s, "
                "feature_schema_version=%s; skipping anomaly detection",
                batch_id,
                summary.dataset_schema,
                summary.feature_schema_version,
            )
            return None

        anomaly_result = predict_anomalies_with_model(
            db,
            model.id,
            feature_matrix,
            feature_schema_version=summary.feature_schema_version,
            dataset_schema=summary.dataset_schema,
        )
        persist_anomaly_results(db, batch_id, anomaly_result, row_numbers)
        return anomaly_result
    except AnomalyInferenceError:
        logger.exception("Batch %s: anomaly detection failed; continuing without it", batch_id)
        return None
    except AnomalyResultPersistenceError:
        logger.exception("Batch %s: failed to persist anomaly results; continuing without it", batch_id)
        return None
    except Exception:
        logger.exception("Batch %s: unexpected error during anomaly detection; continuing without it", batch_id)
        return None


def _run_hybrid_risk_and_explanations(
    db: Session, batch_id: uuid.UUID, inference_result: InferenceResult | None
) -> int:
    """Runs the Step 33-35 derived-analysis layer — hybrid detection
    (`app.services.hybrid_detection`), risk scoring (`app.services.
    risk_scoring`), and explainability (`app.services.explainability`)
    — over whatever `DetectionResult`/`AnomalyResult` rows already exist
    for this batch. Orchestration only: each module owns its own
    computation and persistence; this function just calls them in
    sequence and keeps the whole sequence best-effort, exactly like
    `_run_anomaly_detection`.

    Returns `None` (skips entirely) when `inference_result` is `None` —
    supervised inference did not run for this batch (dataset not
    recognized, or no features persisted), so there is nothing to build
    a hybrid outcome from; this mirrors why `_run_anomaly_detection`
    itself requires persisted features. Does NOT require anomaly
    detection to have succeeded — `app.services.hybrid_detection` already
    handles a missing `AnomalyResult` for a row explicitly (see that
    module's docstring), so a batch with no compatible anomaly detector
    still gets hybrid/risk/explanation rows based on the supervised
    signal alone.

    Deliberately best-effort and non-fatal, like anomaly detection: any
    failure in this layer (a persistence error from any of the three
    modules, or any other unexpected exception) is caught, logged, and
    swallowed — a batch whose supervised inference (and, when
    applicable, anomaly detection) already succeeded must keep
    succeeding identically even if this derived layer cannot complete.
    Returns the number of rows processed (0 if none, e.g. no persisted
    detection results existed) so the caller can log it; never raises.
    """
    if inference_result is None:
        return 0

    try:
        hybrid_results = build_and_persist_hybrid_results(db, batch_id)
        if not hybrid_results:
            return 0

        risk_results = build_and_persist_risk_assessments(db, batch_id, hybrid_results)
        build_and_persist_explanations(db, batch_id, risk_results)
        return len(hybrid_results)
    except Exception:
        logger.exception(
            "Batch %s: hybrid detection/risk scoring/explainability failed; continuing without it", batch_id
        )
        return 0


SUPERVISED_MODEL_TYPE = "random_forest"
"""Matches `app.services.model_training.MODEL_TYPE`. Filtering on this
explicitly (added alongside Step 31's anomaly detector) is what keeps
`_select_ready_model` from ever handing the supervised inference path an
`isolation_forest` row — both model families now share the same
`ModelMetadata` registry table, distinguished only by this column."""


def _select_ready_model(db: Session, dataset_schema: str, feature_schema_version: str) -> ModelMetadata | None:
    """Deterministically selects one `READY` supervised (`random_forest`)
    model compatible with `dataset_schema`/`feature_schema_version`: the
    most recently registered match (`created_at` descending), with ties
    broken by `id` descending so the choice never depends on query-plan or
    insertion-order happenstance. Does not fabricate a match, train one,
    or fall back to an incompatible model — `None` means "nothing
    suitable exists," left for the caller to handle."""
    return db.scalars(
        select(ModelMetadata)
        .where(
            ModelMetadata.status == ModelStatus.READY,
            ModelMetadata.model_type == SUPERVISED_MODEL_TYPE,
            ModelMetadata.dataset_name == dataset_schema,
            ModelMetadata.feature_set_version == feature_schema_version,
        )
        .order_by(ModelMetadata.created_at.desc(), ModelMetadata.id.desc())
    ).first()


def _select_ready_anomaly_model(db: Session, dataset_schema: str, feature_schema_version: str) -> ModelMetadata | None:
    """The anomaly-detector counterpart to `_select_ready_model`: same
    deterministic "most recently registered compatible match" rule,
    filtered to `model_type="isolation_forest"` (see
    `app.services.anomaly_detection_training.MODEL_TYPE`) so it can never
    select the supervised classifier."""
    return db.scalars(
        select(ModelMetadata)
        .where(
            ModelMetadata.status == ModelStatus.READY,
            ModelMetadata.model_type == ANOMALY_MODEL_TYPE,
            ModelMetadata.dataset_name == dataset_schema,
            ModelMetadata.feature_set_version == feature_schema_version,
        )
        .order_by(ModelMetadata.created_at.desc(), ModelMetadata.id.desc())
    ).first()


def _load_feature_matrix_for_batch(db: Session, batch_id: uuid.UUID) -> tuple[list[list[float | None]], list[int]]:
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
    batch-isolation guarantee: another batch's rows are never a match.

    Returns `(feature_matrix, row_numbers)`, index-aligned: `row_numbers[i]`
    is the `MappedFeatureRecord.row_number` that produced
    `feature_matrix[i]` — the same pairing `_run_model_inference` later
    hands to `persist_detection_results` so each `DetectionResult` row
    traces back to the correct original row."""
    rows = db.scalars(
        select(MappedFeatureRecord)
        .where(MappedFeatureRecord.batch_id == batch_id)
        .order_by(MappedFeatureRecord.row_number)
    ).all()
    return [load_feature_vector(row) for row in rows], [row.row_number for row in rows]


def _mark_failed(db: Session, batch: DetectionBatch, message: str) -> None:
    batch.status = ProcessingStatus.FAILED
    batch.error_message = message
    batch.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(batch)
