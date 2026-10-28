"""Anomaly-detector inference: a trained `IsolationForest` artifact
(`app.services.anomaly_detection_training`) + an already-prepared feature
matrix -> validated, deterministic anomaly predictions — entirely
independent of `app.services.model_inference`'s supervised BENIGN/ATTACK
predictions.

    ModelMetadata (registry row, model_type="isolation_forest")
        -> joblib.load(artifact_path) -> artifact payload
                                              |
    feature_matrix (FEATURE_SCHEMA-ordered)  ->  predict_anomalies_with_model()
                                              |
                                      AnomalyInferenceResult

Like `app.services.model_inference`, this module only ever calls
`.transform()` on the training-fitted preprocessing object and
`.predict()`/`.decision_function()` on the trained model — never `.fit()`
or `.fit_transform()`. There is structurally nothing here that could
refit anything on the data handed to it. It is not wired into
`batch_processor.py` by itself, persists nothing, and exposes no API —
see `app.services.anomaly_detection_training` for the matching rationale
on the training side.

What this module reports, and what it deliberately does not: `is_anomaly`
is a real boolean from the model's own `.predict()` (`sklearn`'s
convention: `-1` means the model found the row unusually isolated in
feature space relative to the BENIGN traffic it was trained on; `1` means
it did not). `anomaly_score` is the model's own real `decision_function()`
output — lower means more isolated/anomalous — reported as-is, never
rescaled into a invented "0-100 risk score," never called a probability
(`IsolationForest` does not produce one), and never presented as a claim
that an anomalous row is malicious: an anomaly is a statistical outlier
relative to the training baseline, not a verdict. Risk/severity scoring,
explainability, and any notion of combining this with the supervised
classifier's output are explicitly later-step concerns this module does
not touch.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

import joblib
import numpy as np
from sqlalchemy.orm import Session

from app.models.enums import ModelStatus
from app.models.model_metadata import ModelMetadata
from app.services.feature_extraction import FEATURE_SCHEMA

logger = logging.getLogger("ai_ids")

VALID_ANOMALY_PREDICTION_VALUES = frozenset({1, -1})
"""`sklearn`'s own IsolationForest.predict() convention: `1` = normal,
`-1` = anomaly. Anything else means the loaded artifact is not the
detector this module expects."""

REQUIRED_ARTIFACT_KEYS = frozenset(
    {"model", "imputer", "feature_schema", "feature_schema_version", "dataset_schema", "model_type"}
)

EXPECTED_MODEL_TYPE = "isolation_forest"


class AnomalyInferenceError(Exception):
    """Raised for any anomaly-inference precondition this module cannot
    safely meet — never silently worked around (e.g. by inferring with an
    incompatible artifact, inventing a feature value, or fabricating a
    score). `message` is written to be safe to surface to a caller: no
    filesystem paths, no stack traces, no SQL, no raw exception text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AnomalyPrediction:
    """One row's anomaly-detection outcome. Deliberately minimal: no risk
    score, severity, attack label, or explanation — an anomaly detector
    reports a statistical outlier judgment, nothing more."""

    is_anomaly: bool
    """`True` when the model's own `.predict()` returned `-1` (the row
    was unusually isolated relative to the BENIGN traffic the detector
    was trained on) — never a claim that the row is malicious."""

    anomaly_score: float
    """The model's own real `decision_function()` output — lower means
    more isolated/anomalous. Not a probability, not a percentage, not a
    risk/severity score; reported exactly as the model computed it."""


@dataclass(frozen=True)
class AnomalyInferenceResult:
    """Truthful, structured metadata about one completed anomaly-
    inference run, plus the row-by-row predictions it produced."""

    model_id: uuid.UUID
    model_name: str
    model_version: str
    model_type: str
    feature_schema_version: str
    dataset_schema: str
    sample_count: int
    predictions: list[AnomalyPrediction] = field(default_factory=list)


def predict_anomalies_with_model(
    db: Session,
    model_id: uuid.UUID,
    feature_matrix: Sequence[Sequence[float | None]],
    *,
    feature_schema_version: str,
    dataset_schema: str,
) -> AnomalyInferenceResult:
    """Runs anomaly detection for `model_id` over `feature_matrix` (a
    sequence of already `FEATURE_SCHEMA`-ordered rows — see the module
    docstring on feature ordering, identical discipline to
    `app.services.model_inference`).

    Never retrains the detector, never refits preprocessing, never
    modifies `feature_matrix`, the loaded model, the loaded preprocessing
    object, or any database row.

    Raises `AnomalyInferenceError` for: no registered model found, a
    model with no usable artifact, a corrupt/unreadable/malformed
    artifact, a registry entry whose `model_type` is not
    `"isolation_forest"` (prevents ever accidentally loading the
    supervised classifier through this path, or vice versa), an
    incompatible feature schema, schema version, or dataset schema, a
    feature-count mismatch, a missing preprocessing object, an empty
    input, or an internal inconsistency in the model's output. Never
    exposes a filesystem path, stack trace, SQL, or raw exception text.
    """
    registry_entry = _load_registry_entry(db, model_id)
    payload = _load_artifact_payload(registry_entry)
    _validate_compatibility(payload, feature_matrix, feature_schema_version, dataset_schema, registry_entry)

    X = np.array([list(row) for row in feature_matrix], dtype=float)

    imputer = payload["imputer"]
    model = payload["model"]

    X_transformed = imputer.transform(X)  # transform only — never fit/fit_transform
    raw_predictions = model.predict(X_transformed)  # predict only — never fit

    _validate_predictions(raw_predictions, len(feature_matrix))

    scores = _score(model, X_transformed, len(feature_matrix))

    predictions = [
        AnomalyPrediction(is_anomaly=(int(label) == -1), anomaly_score=float(score))
        for label, score in zip(raw_predictions, scores)
    ]

    return AnomalyInferenceResult(
        model_id=registry_entry.id,
        model_name=registry_entry.model_name,
        model_version=registry_entry.model_version,
        model_type=registry_entry.model_type or payload.get("model_type", ""),
        feature_schema_version=feature_schema_version,
        dataset_schema=dataset_schema,
        sample_count=len(feature_matrix),
        predictions=predictions,
    )


def _load_registry_entry(db: Session, model_id: uuid.UUID) -> ModelMetadata:
    registry_entry = db.get(ModelMetadata, model_id)
    if registry_entry is None:
        raise AnomalyInferenceError("No registered anomaly-detector model was found for the given model id.")
    if registry_entry.status != ModelStatus.READY or not registry_entry.artifact_path:
        raise AnomalyInferenceError("The requested model has no usable trained artifact for anomaly detection.")
    if registry_entry.model_type != EXPECTED_MODEL_TYPE:
        raise AnomalyInferenceError("The requested model is not an anomaly detector.")
    return registry_entry


def _load_artifact_payload(registry_entry: ModelMetadata) -> dict:
    try:
        payload = joblib.load(registry_entry.artifact_path)
    except FileNotFoundError:
        logger.exception("Anomaly-detector artifact file is missing for model %s", registry_entry.id)
        raise AnomalyInferenceError("The trained anomaly-detector artifact could not be found.") from None
    except Exception:
        logger.exception("Anomaly-detector artifact failed to load for model %s", registry_entry.id)
        raise AnomalyInferenceError("The trained anomaly-detector artifact is invalid or could not be read.") from None

    if not isinstance(payload, dict) or not REQUIRED_ARTIFACT_KEYS.issubset(payload.keys()):
        logger.error("Anomaly-detector artifact for model %s is missing required fields", registry_entry.id)
        raise AnomalyInferenceError("The trained anomaly-detector artifact is invalid or could not be read.")

    if payload.get("model_type") != EXPECTED_MODEL_TYPE:
        raise AnomalyInferenceError("The trained artifact is not an anomaly detector.")

    return payload


def _validate_compatibility(
    payload: dict,
    feature_matrix: Sequence[Sequence[float | None]],
    feature_schema_version: str,
    dataset_schema: str,
    registry_entry: ModelMetadata,
) -> None:
    artifact_feature_schema = list(payload["feature_schema"])
    if artifact_feature_schema != list(FEATURE_SCHEMA):
        raise AnomalyInferenceError(
            "The trained anomaly detector's feature schema does not match the current feature schema; "
            "re-train before running anomaly detection."
        )

    if payload["feature_schema_version"] != feature_schema_version:
        raise AnomalyInferenceError(
            "The trained anomaly detector's feature schema version does not match the supplied data's "
            "feature schema version."
        )

    if payload["dataset_schema"] != dataset_schema or (registry_entry.dataset_name or "") != dataset_schema:
        raise AnomalyInferenceError(
            "The trained anomaly detector's dataset schema does not match the supplied data's dataset schema."
        )

    if payload.get("imputer") is None:
        raise AnomalyInferenceError("The trained anomaly-detector artifact is missing its required preprocessing object.")

    model = payload.get("model")
    if (
        model is None
        or not hasattr(model, "predict")
        or not callable(model.predict)
        or not hasattr(model, "decision_function")
        or not callable(model.decision_function)
    ):
        raise AnomalyInferenceError("The trained anomaly-detector artifact does not contain a usable model.")

    if len(feature_matrix) == 0:
        raise AnomalyInferenceError("No feature vectors were supplied for anomaly detection.")

    expected_feature_count = len(artifact_feature_schema)
    for row in feature_matrix:
        if len(row) != expected_feature_count:
            raise AnomalyInferenceError(
                "The supplied feature vectors do not match the trained anomaly detector's expected feature count."
            )


def _validate_predictions(predictions: np.ndarray, expected_count: int) -> None:
    if len(predictions) != expected_count:
        raise AnomalyInferenceError("The model produced a different number of predictions than input rows.")

    distinct_predicted_values = set(np.unique(predictions).tolist())
    if not distinct_predicted_values.issubset(VALID_ANOMALY_PREDICTION_VALUES):
        raise AnomalyInferenceError("The model produced prediction values outside the expected {-1, 1} set.")


def _score(model: object, X_transformed: np.ndarray, expected_count: int) -> list[float]:
    try:
        scores = model.decision_function(X_transformed)
    except Exception:
        logger.exception("decision_function failed during anomaly inference")
        raise AnomalyInferenceError("The model failed to produce an anomaly score.") from None

    scores = np.asarray(scores)
    if scores.ndim != 1 or scores.shape[0] != expected_count or not np.all(np.isfinite(scores)):
        raise AnomalyInferenceError("The model produced a malformed anomaly score.")

    return [float(s) for s in scores]


__all__ = ["AnomalyInferenceError", "AnomalyPrediction", "AnomalyInferenceResult", "predict_anomalies_with_model"]
