"""Model evaluation: a trained model artifact (Step 22) + the untouched
test partition (Step 23) -> a structured, truthful evaluation result.

    ModelMetadata (registry row) -> joblib.load(artifact_path) -> artifact payload
                                                                        |
    DatasetPartition (.test, from app.services.dataset_split)  ->  evaluate_model()
                                                                        |
                                                                EvaluationResult

This module establishes the trustworthy boundary between *training* a
model and *assessing* it: it loads an already-trained model and an
already-fitted preprocessing object (both written once, at training time,
by `app.services.model_training`) and only ever calls `.transform()` /
`.predict()` on them — never `.fit()` or `.fit_transform()`. There is
structurally nothing in this module that could refit anything on test
data, because this module never calls a fitting method at all.

Why the test set must stay untouched: `app.services.dataset_split`
prepares `test` specifically so no training, preprocessing, or model-
selection decision ever saw it. If evaluation itself mutated the test
partition, retrained the model, or re-fit preprocessing using test data,
every number this module reports would be optimistic by construction and
the whole point of holding out a test set would be lost. This module
reads `test_partition` and the loaded artifact; it never writes to either.

Metrics are evaluation results for one specific held-out test dataset —
real numbers computed from this run's own predictions, never a production
IDS performance claim, never fabricated, and never computed when there is
not enough valid test data to compute them safely (see `EvaluationError`).

Non-goals (this module does not do any of the following): retrain a
model, refit preprocessing, serve an inference/prediction API, create
`DetectionResult` rows, or compute/display any attack confidence,
severity, risk, or threat score.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import joblib
import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
    precision_score,
    recall_score,
)
from sqlalchemy.orm import Session

from app.models.enums import ModelStatus
from app.models.model_metadata import ModelMetadata
from app.services.dataset_split import DatasetPartition
from app.services.feature_extraction import FEATURE_SCHEMA

logger = logging.getLogger("ai_ids")

VALID_LABEL_VALUES = frozenset({0, 1})
"""The only values this binary classifier may legitimately output; see
`BinaryLabel` in `app.services.dataset_adapters.label_mapping`. Anything
else from `model.predict()` means the loaded artifact is not the
classifier this module expects — `EvaluationError`, never guessed past."""


class EvaluationError(Exception):
    """Raised for any evaluation precondition this module cannot safely
    meet — never silently worked around (e.g. by evaluating on an empty
    set, skipping a failed compatibility check, or guessing a missing
    value). `message` is written to be safe to surface to a caller: no
    filesystem paths, no stack traces, no SQL, no raw exception text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class EvaluationResult:
    """Truthful, structured metadata about one completed evaluation run.
    Every field is either an identity/count value or a metric computed
    from this run's own real predictions on the held-out test partition —
    nothing here is invented. Deliberately excludes anything that could
    leak sensitive or unnecessary detail: no filesystem paths, no raw
    feature vectors, no uploaded-file content, no credentials.
    """

    model_id: uuid.UUID
    model_name: str
    model_version: str
    model_type: str
    feature_schema_version: str
    dataset_schema: str
    test_sample_count: int
    class_counts: dict[str, int]
    """Real `{"benign": N, "attack": M}` counts from the test partition
    actually evaluated — never inferred or estimated."""
    metrics: dict[str, float]
    """`{"accuracy", "precision", "recall", "f1"}` — computed from actual
    predictions compared against actual test labels, `zero_division=0`."""
    confusion_matrix: dict[str, int]
    """`{"tp", "tn", "fp", "fn"}`, ATTACK (`1`) as the positive class."""
    class_metrics: dict[str, dict[str, float]]
    """Per-class `{"benign": {...}, "attack": {...}}`, each with its own
    `precision`/`recall`/`f1`/`support` — so BENIGN and ATTACK performance
    can be told apart rather than inferred from the combined metrics or
    the class distribution alone."""
    evaluation_config: dict = field(default_factory=dict)
    """Non-sensitive configuration this evaluation ran with (e.g. the
    positive-class convention and zero-division policy) — never a
    filesystem path, never training hyperparameters (those belong to the
    model's own `training_config`, not to this evaluation)."""
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def evaluate_model(
    db: Session,
    model_id: uuid.UUID,
    test_partition: DatasetPartition,
    *,
    feature_schema_version: str,
    dataset_schema: str,
) -> EvaluationResult:
    """Evaluates the registered model `model_id` against `test_partition`
    (the untouched `test` partition of a `PreparedDataset` — see
    `app.services.dataset_split.prepare_training_data`).

    Never retrains the model, never refits preprocessing, never modifies
    `test_partition`, the loaded model, the loaded preprocessing object,
    or any database row. Only ever calls `.transform()` on the training-
    fitted preprocessing and `.predict()` on the trained model.

    `feature_schema_version` and `dataset_schema` describe the test data
    being evaluated (the same values the `TrainingDataset`/`PreparedDataset`
    that produced `test_partition` carried) — compared against the
    artifact's own recorded values as a compatibility check, not derived
    from the artifact itself.

    Raises `EvaluationError` for: no registered model found, a model with
    no usable artifact, a corrupt/unreadable artifact, an incompatible
    feature schema or schema version, an empty test set, a test set with
    fewer than two classes present, or an internal inconsistency in the
    model's output (wrong length, values outside `{0, 1}`). Never exposes
    a filesystem path, stack trace, SQL, or raw exception text — see that
    module's own docstring on logging.
    """
    registry_entry = _load_registry_entry(db, model_id)
    payload = _load_artifact_payload(registry_entry)
    _validate_compatibility(payload, test_partition, feature_schema_version, dataset_schema, registry_entry)

    X_test, y_test = _validate_test_set(test_partition)

    imputer = payload["imputer"]
    model = payload["model"]

    X_test_transformed = imputer.transform(X_test)  # transform only — never fit/fit_transform
    predictions = model.predict(X_test_transformed)  # predict only — never fit

    _validate_predictions(predictions, y_test)

    metrics = {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "precision": float(precision_score(y_test, predictions, zero_division=0)),
        "recall": float(recall_score(y_test, predictions, zero_division=0)),
        "f1": float(f1_score(y_test, predictions, zero_division=0)),
    }

    cm = confusion_matrix(y_test, predictions, labels=[0, 1])
    tn, fp, fn, tp = (int(v) for v in cm.ravel())
    confusion = {"tp": tp, "tn": tn, "fp": fp, "fn": fn}

    class_metrics = _build_class_metrics(y_test, predictions)

    class_counts = {
        "benign": int(np.sum(y_test == 0)),
        "attack": int(np.sum(y_test == 1)),
    }

    return EvaluationResult(
        model_id=registry_entry.id,
        model_name=registry_entry.model_name,
        model_version=registry_entry.model_version,
        model_type=registry_entry.model_type or payload.get("model_type", ""),
        feature_schema_version=feature_schema_version,
        dataset_schema=dataset_schema,
        test_sample_count=len(y_test),
        class_counts=class_counts,
        metrics=metrics,
        confusion_matrix=confusion,
        class_metrics=class_metrics,
        evaluation_config={"positive_class": "attack", "zero_division": 0},
    )


def _load_registry_entry(db: Session, model_id: uuid.UUID) -> ModelMetadata:
    registry_entry = db.get(ModelMetadata, model_id)
    if registry_entry is None:
        raise EvaluationError("No registered model was found for the given model id.")
    if registry_entry.status != ModelStatus.READY or not registry_entry.artifact_path:
        raise EvaluationError("The requested model has no usable trained artifact to evaluate.")
    return registry_entry


def _load_artifact_payload(registry_entry: ModelMetadata) -> dict:
    try:
        payload = joblib.load(registry_entry.artifact_path)
    except FileNotFoundError:
        logger.exception("Model artifact file is missing for model %s", registry_entry.id)
        raise EvaluationError("The trained model artifact could not be found.") from None
    except Exception:
        logger.exception("Model artifact failed to load for model %s", registry_entry.id)
        raise EvaluationError("The trained model artifact is invalid or could not be read.") from None

    required_keys = {"model", "imputer", "feature_schema", "feature_schema_version", "dataset_schema", "model_type"}
    if not isinstance(payload, dict) or not required_keys.issubset(payload.keys()):
        logger.error("Model artifact for model %s is missing required fields", registry_entry.id)
        raise EvaluationError("The trained model artifact is invalid or could not be read.")

    return payload


def _validate_compatibility(
    payload: dict,
    test_partition: DatasetPartition,
    feature_schema_version: str,
    dataset_schema: str,
    registry_entry: ModelMetadata,
) -> None:
    artifact_feature_schema = list(payload["feature_schema"])
    if artifact_feature_schema != list(FEATURE_SCHEMA):
        raise EvaluationError(
            "The trained model's feature schema does not match the current feature schema; re-train "
            "before evaluating."
        )

    if payload["feature_schema_version"] != feature_schema_version:
        raise EvaluationError(
            "The trained model's feature schema version does not match the test data's feature schema "
            "version; re-train or re-prepare the test data before evaluating."
        )

    if payload["dataset_schema"] != dataset_schema or (registry_entry.dataset_name or "") != dataset_schema:
        raise EvaluationError(
            "The trained model's dataset schema does not match the test data's dataset schema."
        )

    expected_feature_count = len(artifact_feature_schema)
    for row in test_partition.feature_matrix:
        if len(row) != expected_feature_count:
            raise EvaluationError(
                "The test data's feature count does not match the trained model's expected feature count."
            )


def _validate_test_set(test_partition: DatasetPartition) -> tuple[np.ndarray, np.ndarray]:
    if test_partition.sample_count == 0:
        raise EvaluationError("No test samples are available to evaluate.")

    y_test = np.array([int(label) for label in test_partition.labels], dtype=int)
    if len(set(y_test.tolist())) < 2:
        raise EvaluationError("The test set does not contain both classes required for evaluation.")

    X_test = np.array(test_partition.feature_matrix, dtype=float)
    return X_test, y_test


def _validate_predictions(predictions: np.ndarray, y_test: np.ndarray) -> None:
    if len(predictions) != len(y_test):
        raise EvaluationError("The model produced a different number of predictions than there are test labels.")

    distinct_predicted_values = set(np.unique(predictions).tolist())
    if not distinct_predicted_values.issubset(VALID_LABEL_VALUES):
        raise EvaluationError("The model produced prediction values outside the expected label set.")


def _build_class_metrics(y_test: np.ndarray, predictions: np.ndarray) -> dict[str, dict[str, float]]:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_test, predictions, labels=[0, 1], zero_division=0
    )
    return {
        "benign": {
            "precision": float(precision[0]),
            "recall": float(recall[0]),
            "f1": float(f1[0]),
            "support": int(support[0]),
        },
        "attack": {
            "precision": float(precision[1]),
            "recall": float(recall[1]),
            "f1": float(f1[1]),
            "support": int(support[1]),
        },
    }


__all__ = ["EvaluationError", "EvaluationResult", "evaluate_model"]
