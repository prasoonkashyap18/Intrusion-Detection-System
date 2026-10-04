"""Model inference: a trained model artifact (Step 22) + an already-
prepared feature matrix -> validated, deterministic predictions.

    ModelMetadata (registry row) -> joblib.load(artifact_path) -> artifact payload
                                                                        |
    feature_matrix (FEATURE_SCHEMA-ordered)              ->  predict_with_model()
                                                                        |
                                                                InferenceResult

This module is the reusable prediction primitive the rest of the system
will eventually call — it does not call itself, is not wired into
`batch_processor.py`, and does not persist anything. Like
`app.services.model_evaluation`, it only ever calls `.transform()` on the
training-fitted preprocessing object and `.predict()`/`.predict_proba()`
on the trained model — never `.fit()` or `.fit_transform()`. There is
structurally nothing in this module that could refit anything on the
data handed to it.

Preprocessing correctness: the exact fitted `imputer` stored in the
artifact at training time (see `app.services.model_training`) is the only
preprocessing ever applied here — never a freshly constructed one, never
one fit on the input. Feature ordering is the caller's responsibility
(the same discipline `app.services.training_data`/`app.services.
feature_persistence` already establish): a feature matrix must already be
`FEATURE_SCHEMA`-ordered before it reaches this module, and this module
never reorders, invents, or imputes a value itself — a missing value
(`None`) stays `None` until the stored imputer handles it, exactly as
during training and evaluation.

Binary classification semantics: `BENIGN = 0`, `ATTACK = 1` (see
`app.services.dataset_adapters.label_mapping.BinaryLabel`). This module
never invents a third class. A probability is only ever reported when the
loaded model genuinely exposes `predict_proba` and that output is
internally consistent (right shape, a `1` class present, values in
`[0, 1]`) — reported as `attack_probability` (the probability of the
ATTACK class), never called a "confidence score," since nothing about a
`RandomForestClassifier`'s vote fractions makes that a correct name for
the concept of confidence a later risk-scoring step might introduce.

No dataset-specific branching lives here: this module consumes the
canonical, already-mapped feature representation and a plain
`dataset_schema` string used only for an artifact-compatibility
*comparison*, never a decision branch. Dataset-specific knowledge (how a
raw label is interpreted, which columns a dataset maps to which feature)
stays in `app.services.dataset_adapters`.

Non-goals (deliberately not implemented here, deferred to later steps):
`DetectionResult` persistence, an inference API/endpoint, integration
into `batch_processor.py`, anomaly detection, risk/severity/confidence
scoring, explainability, alerting, or any other detection-pipeline
concept. This module returns a prediction; it does not decide what the
rest of the system should do with one.
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

VALID_LABEL_VALUES = frozenset({0, 1})
"""The only values this binary classifier may legitimately output; see
`BinaryLabel` in `app.services.dataset_adapters.label_mapping`. Anything
else from `model.predict()` means the loaded artifact is not the
classifier this module expects — `InferenceError`, never guessed past."""

ATTACK_CLASS_VALUE = 1

REQUIRED_ARTIFACT_KEYS = frozenset(
    {"model", "imputer", "feature_schema", "feature_schema_version", "dataset_schema", "model_type"}
)


class InferenceError(Exception):
    """Raised for any inference precondition this module cannot safely
    meet — never silently worked around (e.g. by inferring on an
    incompatible artifact, inventing a feature value, or fabricating a
    probability). `message` is written to be safe to surface to a
    caller: no filesystem paths, no stack traces, no SQL, no raw
    exception text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Prediction:
    """One row's inference outcome. Deliberately minimal: no risk score,
    severity, anomaly score, or explanation — those are later steps'
    concerns, not this one's."""

    predicted_label: int
    """`0` (BENIGN) or `1` (ATTACK) — validated against `VALID_LABEL_VALUES`
    before this object is ever constructed."""

    prediction_name: str
    """`"benign"` or `"attack"` — the canonical class name for
    `predicted_label`, never a free-text or dataset-specific label."""

    attack_probability: float | None = None
    """The probability of the ATTACK class, in `[0, 1]`, only when the
    loaded model genuinely supports `predict_proba` and its output is
    internally consistent — `None` otherwise. Never fabricated, and never
    called a "confidence score" (see module docstring)."""


@dataclass(frozen=True)
class InferenceResult:
    """Truthful, structured metadata about one completed inference run,
    plus the row-by-row predictions it produced. Enough for provenance
    (which model, which schema) without holding anything sensitive."""

    model_id: uuid.UUID
    model_name: str
    model_version: str
    model_type: str
    feature_schema_version: str
    dataset_schema: str
    sample_count: int
    predictions: list[Prediction] = field(default_factory=list)


def predict_with_model(
    db: Session,
    model_id: uuid.UUID,
    feature_matrix: Sequence[Sequence[float | None]],
    *,
    feature_schema_version: str,
    dataset_schema: str,
) -> InferenceResult:
    """Runs inference for `model_id` over `feature_matrix` (a sequence of
    already `FEATURE_SCHEMA`-ordered rows — see the module docstring on
    feature ordering).

    Never retrains the model, never refits preprocessing, never modifies
    `feature_matrix`, the loaded model, the loaded preprocessing object,
    or any database row. Only ever calls `.transform()` on the training-
    fitted preprocessing and `.predict()`/`.predict_proba()` on the
    trained model.

    `feature_schema_version` and `dataset_schema` describe the supplied
    data — compared against the artifact's own recorded values as a
    compatibility check, not derived from the artifact itself.

    Raises `InferenceError` for: no registered model found, a model with
    no usable artifact, a corrupt/unreadable/malformed artifact, an
    incompatible feature schema, schema version, or dataset schema, a
    feature-count mismatch, a missing preprocessing object, an empty
    input, or an internal inconsistency in the model's output (wrong
    prediction count, prediction values outside `{0, 1}`, malformed
    probability output). Never exposes a filesystem path, stack trace,
    SQL, or raw exception text.
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

    attack_probabilities = _maybe_predict_proba(model, X_transformed, len(feature_matrix))

    predictions = [
        Prediction(
            predicted_label=int(label),
            prediction_name="attack" if int(label) == ATTACK_CLASS_VALUE else "benign",
            attack_probability=(attack_probabilities[i] if attack_probabilities is not None else None),
        )
        for i, label in enumerate(raw_predictions)
    ]

    return InferenceResult(
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
        raise InferenceError("No registered model was found for the given model id.")
    if registry_entry.status != ModelStatus.READY or not registry_entry.artifact_path:
        raise InferenceError("The requested model has no usable trained artifact for inference.")
    return registry_entry


def _load_artifact_payload(registry_entry: ModelMetadata) -> dict:
    try:
        payload = joblib.load(registry_entry.artifact_path)
    except FileNotFoundError:
        logger.exception("Model artifact file is missing for model %s", registry_entry.id)
        raise InferenceError("The trained model artifact could not be found.") from None
    except Exception:
        logger.exception("Model artifact failed to load for model %s", registry_entry.id)
        raise InferenceError("The trained model artifact is invalid or could not be read.") from None

    if not isinstance(payload, dict) or not REQUIRED_ARTIFACT_KEYS.issubset(payload.keys()):
        logger.error("Model artifact for model %s is missing required fields", registry_entry.id)
        raise InferenceError("The trained model artifact is invalid or could not be read.")

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
        raise InferenceError(
            "The trained model's feature schema does not match the current feature schema; re-train "
            "before running inference."
        )

    if payload["feature_schema_version"] != feature_schema_version:
        raise InferenceError(
            "The trained model's feature schema version does not match the supplied data's feature "
            "schema version."
        )

    if payload["dataset_schema"] != dataset_schema or (registry_entry.dataset_name or "") != dataset_schema:
        raise InferenceError("The trained model's dataset schema does not match the supplied data's dataset schema.")

    if payload.get("imputer") is None:
        raise InferenceError("The trained model artifact is missing its required preprocessing object.")

    model = payload.get("model")
    if model is None or not hasattr(model, "predict") or not callable(model.predict):
        raise InferenceError("The trained model artifact does not contain a usable model.")

    if len(feature_matrix) == 0:
        raise InferenceError("No feature vectors were supplied for inference.")

    expected_feature_count = len(artifact_feature_schema)
    for row in feature_matrix:
        if len(row) != expected_feature_count:
            raise InferenceError(
                "The supplied feature vectors do not match the trained model's expected feature count."
            )


def _validate_predictions(predictions: np.ndarray, expected_count: int) -> None:
    if len(predictions) != expected_count:
        raise InferenceError("The model produced a different number of predictions than input rows.")

    distinct_predicted_values = set(np.unique(predictions).tolist())
    if not distinct_predicted_values.issubset(VALID_LABEL_VALUES):
        raise InferenceError("The model produced prediction values outside the expected label set.")


def _maybe_predict_proba(model: object, X_transformed: np.ndarray, expected_count: int) -> list[float] | None:
    """Returns the probability of the ATTACK class per row, or `None` if
    the model does not genuinely support probability output. Raises
    `InferenceError` (rather than silently falling back to `None`) if the
    model claims `predict_proba` support but its output is internally
    inconsistent — a malformed artifact, not an absence of the feature.
    """
    if not hasattr(model, "predict_proba") or not callable(model.predict_proba):
        return None

    try:
        proba = model.predict_proba(X_transformed)
    except Exception:
        logger.exception("predict_proba failed during inference")
        raise InferenceError("The model failed to produce probability output.") from None

    proba = np.asarray(proba)
    classes = getattr(model, "classes_", None)
    if (
        classes is None
        or proba.ndim != 2
        or proba.shape[0] != expected_count
        or ATTACK_CLASS_VALUE not in list(classes)
    ):
        raise InferenceError("The model produced malformed probability output.")

    attack_index = list(classes).index(ATTACK_CLASS_VALUE)
    attack_probabilities = proba[:, attack_index]
    if not np.all((attack_probabilities >= 0.0) & (attack_probabilities <= 1.0)):
        raise InferenceError("The model produced malformed probability output.")

    return [float(p) for p in attack_probabilities]


__all__ = ["InferenceError", "Prediction", "InferenceResult", "predict_with_model"]
