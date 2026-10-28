"""Persists `app.services.anomaly_inference.AnomalyInferenceResult`
predictions as `AnomalyResult` rows — the anomaly-detection counterpart
to `app.services.detection_result_persistence.persist_detection_results`,
following the exact same pattern:

    AnomalyInferenceResult (app.services.anomaly_inference)
        |
    persist_anomaly_results()
        |
    AnomalyResult rows  <- one per prediction, linked to DetectionBatch
                            and ModelMetadata

This module performs NO inference and NO feature extraction: it only
writes rows from an `AnomalyInferenceResult` a caller already produced
(see `app.services.batch_processor._run_anomaly_detection`) — it never
calls `predict_anomalies_with_model` itself, never reads a batch's CSV,
and never queries `MappedFeatureRecord` for anything beyond what the
caller already supplies as `row_numbers`. The full feature vector is
never duplicated onto `AnomalyResult`; it already exists on
`MappedFeatureRecord`, reachable by `batch_id` + `row_number`.

Transaction boundary: `persist_anomaly_results` both stages and commits
in one call, exactly like `persist_detection_results` — a failure here
rolls back only this call's own uncommitted rows. Unlike supervised
detection-result persistence, a failure here is caught non-fatally by
the caller (`_run_anomaly_detection`'s existing best-effort boundary) —
anomaly detection, now including its persistence, must never break
existing supervised detection (see that function's docstring).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.anomaly_result import AnomalyResult
from app.services.anomaly_inference import AnomalyInferenceResult

logger = logging.getLogger("ai_ids")


class AnomalyResultPersistenceError(Exception):
    """Raised when this module cannot safely persist a set of anomaly
    results — never partially, and never silently. `message` is written
    to be safe to surface to a caller: no filesystem paths, no stack
    traces, no SQL, no raw exception text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def persist_anomaly_results(
    db: Session,
    batch_id: uuid.UUID,
    anomaly_result: AnomalyInferenceResult,
    row_numbers: Sequence[int],
) -> list[AnomalyResult]:
    """Persists one `AnomalyResult` row per prediction in
    `anomaly_result`, each tied to `batch_id` and `anomaly_result.
    model_id`, with `row_numbers[i]` identifying which of the batch's
    original rows `anomaly_result.predictions[i]` describes (the same
    order `app.services.batch_processor._load_feature_matrix_for_batch`
    already establishes).

    Returns `[]` without writing anything for an empty `anomaly_result`
    (nothing to persist is not an error). Commits all rows in one
    transaction: either every row is written, or (on a database error,
    or a duplicate `(batch_id, row_number)` pair already present) none
    are — `db.rollback()` runs before raising
    `AnomalyResultPersistenceError`, so a failed attempt never leaves a
    partial set behind.

    Raises `AnomalyResultPersistenceError` if `row_numbers` does not
    have exactly one entry per prediction, or if the commit fails for
    any reason (including a unique-constraint violation from
    re-persisting an already-persisted row).
    """
    if anomaly_result.sample_count == 0 or not anomaly_result.predictions:
        return []

    if len(row_numbers) != len(anomaly_result.predictions):
        raise AnomalyResultPersistenceError(
            "The number of row numbers supplied does not match the number of predictions to persist."
        )

    rows = [
        AnomalyResult(
            batch_id=batch_id,
            model_id=anomaly_result.model_id,
            row_number=row_number,
            is_anomaly=prediction.is_anomaly,
            anomaly_score=prediction.anomaly_score,
        )
        for row_number, prediction in zip(row_numbers, anomaly_result.predictions)
    ]

    try:
        db.add_all(rows)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist anomaly results for batch %s", batch_id)
        raise AnomalyResultPersistenceError("Unable to persist anomaly results for this batch.") from None

    return rows


__all__ = ["AnomalyResultPersistenceError", "persist_anomaly_results"]
