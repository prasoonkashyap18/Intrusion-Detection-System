"""Persists `app.services.model_inference.InferenceResult` predictions as
`DetectionResult` rows — the final step of the integration Step 26 wired
up, now writing its in-memory result to the database instead of only
logging and discarding it.

    InferenceResult (app.services.model_inference)
        |
    persist_detection_results()
        |
    DetectionResult rows  <- one per prediction, linked to DetectionBatch
                              and ModelMetadata

This module performs NO inference and NO feature extraction: it only
writes rows from an `InferenceResult` a caller already produced (see
`app.services.batch_processor._run_model_inference`) — it never calls
`predict_with_model` itself, never reads a batch's CSV, and never queries
`MappedFeatureRecord` for anything beyond what the caller already supplies
as `row_numbers`. The full feature vector is never duplicated onto
`DetectionResult`; it already exists on `MappedFeatureRecord`, reachable
by `batch_id` + `row_number`.

Transaction boundary: `persist_detection_results` both stages and commits
in one call (unlike `app.services.feature_persistence.
persist_mapped_features`, which only stages) — detection-result
persistence is a separate, later transaction from feature persistence,
which has already committed by the time inference runs (see
`app.services.batch_processor`). A failure here rolls back only this
call's own uncommitted rows; it never reaches back into the already-
durable `MappedFeatureRecord` rows or re-triggers inference.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.detection_result import DetectionResult
from app.services.model_inference import InferenceResult

logger = logging.getLogger("ai_ids")


class DetectionResultPersistenceError(Exception):
    """Raised when this module cannot safely persist a set of detection
    results — never partially, and never silently. `message` is written
    to be safe to surface to a caller: no filesystem paths, no stack
    traces, no SQL, no raw exception text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def persist_detection_results(
    db: Session,
    batch_id: uuid.UUID,
    inference_result: InferenceResult,
    row_numbers: Sequence[int],
) -> list[DetectionResult]:
    """Persists one `DetectionResult` row per prediction in
    `inference_result`, each tied to `batch_id` and
    `inference_result.model_id`, with `row_numbers[i]` identifying which
    of the batch's original rows `inference_result.predictions[i]`
    describes (the same order `app.services.batch_processor.
    _load_feature_matrix_for_batch` already establishes — deterministic,
    `row_number`-based, never dict/insertion order).

    Returns `[]` without writing anything for an empty `inference_result`
    (nothing to persist is not an error). Commits all rows in one
    transaction: either every row is written, or (on a database error, or
    a duplicate `(batch_id, row_number)` pair already present) none are —
    `db.rollback()` runs before raising `DetectionResultPersistenceError`,
    so a failed attempt never leaves a partial set behind.

    Raises `DetectionResultPersistenceError` if `row_numbers` does not
    have exactly one entry per prediction (an internal-consistency
    precondition, not something a caller should ever need to recover
    from), or if the commit fails for any reason (including a unique-
    constraint violation from re-persisting an already-persisted row).
    """
    if inference_result.sample_count == 0 or not inference_result.predictions:
        return []

    if len(row_numbers) != len(inference_result.predictions):
        raise DetectionResultPersistenceError(
            "The number of row numbers supplied does not match the number of predictions to persist."
        )

    rows = [
        DetectionResult(
            batch_id=batch_id,
            model_id=inference_result.model_id,
            row_number=row_number,
            predicted_label=prediction.predicted_label,
            prediction_name=prediction.prediction_name,
            attack_probability=prediction.attack_probability,
        )
        for row_number, prediction in zip(row_numbers, inference_result.predictions)
    ]

    try:
        db.add_all(rows)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist detection results for batch %s", batch_id)
        raise DetectionResultPersistenceError("Unable to persist detection results for this batch.") from None

    return rows


__all__ = ["DetectionResultPersistenceError", "persist_detection_results"]
