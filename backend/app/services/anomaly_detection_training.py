"""Unsupervised anomaly-detector training: `TrainingDataset` -> a trained,
persisted, registered `IsolationForest`, kept entirely separate from the
supervised `RandomForestClassifier` pipeline (`app.services.model_training`).

    TrainingDataset (app.services.training_data)
        |
    keep only BENIGN-labeled rows (the "normal" baseline)
        |
    deterministic train/validation split (no stratification needed —
        single class by construction)
        |
    fit imputer on the TRAIN split only
        |
    train IsolationForest (fixed, documented configuration)
        |
    self-consistency check on the VALIDATION split (real predictions only)
        |
    write artifact (joblib) -> register in ModelMetadata (only after the
        artifact write succeeds)
        |
    AnomalyTrainingResult

Why train on BENIGN-only data: an unsupervised anomaly detector is only
meaningful if it has learned what "normal" looks like — training it on a
mix of benign and attack rows would teach it to treat both as ordinary,
defeating the point. This is *not* a supervised classifier: `BinaryLabel`
(already computed by `app.services.dataset_adapters.label_mapping`,
exactly as the supervised pipeline already uses it) is used here only to
*select* which already-persisted rows form the normal baseline — the
`IsolationForest` itself never sees a label, never fits against one, and
produces no class prediction. Using the existing label as a selection
filter is not "fabricating an attack label"; no label is invented,
guessed, or attached to anything that did not already have one.

This module is kept completely independent of `app.services.
model_training`: it shares only the already-existing `TrainingDataset`
input type and the already-existing `ModelMetadata` registry table
(distinguished by `model_type="isolation_forest"`, never
`"random_forest"`), never the supervised model's training/evaluation
logic. It does not persist a `DetectionResult`, does not expose a
prediction API, and does not compute or claim any risk/severity score —
see `app.services.anomaly_inference` for what an anomaly prediction
actually contains.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.enums import ModelStatus
from app.models.model_metadata import ModelMetadata
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA
from app.services.training_data import TrainingDataset

logger = logging.getLogger("ai_ids")

MODEL_TYPE = "isolation_forest"
RANDOM_SEED = 42
VALIDATION_FRACTION = 0.2
MIN_NORMAL_SAMPLES = 20
"""Needs to comfortably survive a 20% validation split (at least a
handful of rows on each side) and give `IsolationForest` enough normal
examples to characterize "normal" meaningfully. Arbitrary but
conservative; `ModelTrainingError` is still raised with the real counts
if an actual dataset falls short, never silently trained on too little."""

ISOLATION_FOREST_PARAMS: dict[str, object] = {
    "n_estimators": 100,
    "contamination": "auto",
    "random_state": RANDOM_SEED,
}
"""Fixed and explicit on purpose — one deterministic baseline
configuration, not a hyperparameter search, mirroring
`app.services.model_training.RANDOM_FOREST_PARAMS`'s own convention."""

IMPUTATION_STRATEGY = "median"
"""Same convention and same `keep_empty_features=True` rationale as
`app.services.model_training.IMPUTATION_STRATEGY` — see that module's
docstring for why a column entirely missing from the training split must
be kept (filled with 0.0) rather than silently dropped."""


class AnomalyTrainingError(Exception):
    """Raised for any anomaly-training precondition this module cannot
    safely meet — never silently worked around. `message` is written to
    be safe to surface to a caller: no filesystem paths, no stack traces,
    no SQL."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AnomalyTrainingResult:
    """Truthful metadata about one completed anomaly-detector training
    run. Every field is either an actual count/configuration value or a
    number computed from this run's own real validation predictions —
    nothing here is invented, and nothing here is a risk or severity
    score."""

    model_id: uuid.UUID
    model_name: str
    model_version: str
    model_type: str
    feature_schema_version: str
    dataset_schema: str
    training_sample_count: int
    validation_sample_count: int
    feature_count: int
    excluded_non_benign_count: int
    """Persisted rows that were loaded but excluded because they were not
    labeled BENIGN — this detector trains only on the normal baseline."""
    excluded_unmappable_label_count: int
    training_config: dict = field(default_factory=dict)
    preprocessing_config: dict = field(default_factory=dict)
    random_seed: int = RANDOM_SEED
    validation_normal_rate: float = 0.0
    """The fraction of the held-out validation split (itself all
    genuinely BENIGN rows) this run's own trained detector predicted as
    normal — a real self-consistency number from real predictions, never
    a production false-positive-rate claim and never compared against any
    ground-truth anomaly label (none exists)."""
    artifact_path: str = ""


def train_anomaly_detector(
    db: Session,
    dataset: TrainingDataset,
    *,
    model_name: str = "ai-ids-anomaly-detector",
    model_version: str | None = None,
    artifact_dir: Path,
) -> AnomalyTrainingResult:
    """Trains, persists, and registers one `IsolationForest` anomaly
    detector from an already-loaded `TrainingDataset` (see
    `app.services.training_data.load_training_data`) — using only its
    BENIGN-labeled rows as the normal baseline (see module docstring).

    Raises `AnomalyTrainingError` for: too few BENIGN rows to train and
    validate on, training rows spanning more than one dataset schema (a
    modeling decision outside this step's scope — one detector per
    dataset family, matching the supervised pipeline's own rule), or any
    failure writing/registering the artifact (the orphaned artifact is
    removed on a registration failure, exactly as `app.services.
    model_training.train_baseline_model` already does — see "Transaction
    safety" in backend/README.md).
    """
    normal_rows, excluded_non_benign = _select_benign_rows(dataset)
    dataset_schema = _validate_single_dataset_schema(dataset)
    _validate_sample_count(len(normal_rows))

    X = np.array(normal_rows, dtype=float)
    try:
        X_train, X_val = train_test_split(X, test_size=VALIDATION_FRACTION, random_state=RANDOM_SEED)
    except ValueError:
        logger.exception("Train/validation split failed for an anomaly-detector training run")
        raise AnomalyTrainingError(
            "Unable to create a train/validation split from the available normal-traffic data."
        ) from None

    imputer = SimpleImputer(strategy=IMPUTATION_STRATEGY, keep_empty_features=True)
    X_train_imputed = imputer.fit_transform(X_train)  # fitted on TRAIN ONLY
    X_val_imputed = imputer.transform(X_val)  # validation never influences the fit

    model = IsolationForest(**ISOLATION_FOREST_PARAMS)
    model.fit(X_train_imputed)

    validation_predictions = model.predict(X_val_imputed)  # 1 = normal, -1 = anomaly
    validation_normal_rate = float(np.mean(validation_predictions == 1)) if len(validation_predictions) else 0.0

    model_id = uuid.uuid4()
    resolved_version = model_version or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_path = artifact_dir / f"{model_id}.joblib"

    training_config = {
        "validation_fraction": VALIDATION_FRACTION,
        "random_seed": RANDOM_SEED,
        "model_type": MODEL_TYPE,
        "model_params": dict(ISOLATION_FOREST_PARAMS),
        "feature_schema": list(FEATURE_SCHEMA),
    }
    preprocessing_config = {"strategy": IMPUTATION_STRATEGY, "imputer_statistics": imputer.statistics_.tolist()}

    artifact_payload = {
        "model": model,
        "imputer": imputer,
        "feature_schema": list(FEATURE_SCHEMA),
        "feature_schema_version": dataset.feature_schema_version,
        "dataset_schema": dataset_schema,
        "model_type": MODEL_TYPE,
        "training_config": training_config,
        "preprocessing_config": preprocessing_config,
    }

    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(artifact_payload, artifact_path)
    except OSError:
        logger.exception("Failed to write anomaly-detector artifact for model %s", model_id)
        raise AnomalyTrainingError("Unable to save the trained anomaly-detector artifact.") from None

    try:
        registry_entry = ModelMetadata(
            id=model_id,
            model_name=model_name,
            model_version=resolved_version,
            model_type=MODEL_TYPE,
            dataset_name=dataset_schema,
            feature_set_version=dataset.feature_schema_version,
            trained_at=datetime.now(timezone.utc),
            evaluation_metrics={"validation_normal_rate": validation_normal_rate},
            artifact_path=str(artifact_path),
            status=ModelStatus.READY,
            training_config=training_config,
            training_batch_ids=[str(batch_id) for batch_id in sorted(set(dataset.batch_ids), key=str)],
            is_active=False,
        )
        db.add(registry_entry)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to register anomaly-detector model %s; removing orphaned artifact", model_id)
        _remove_orphaned_artifact(artifact_path)
        raise AnomalyTrainingError("Unable to register the trained anomaly-detector model.") from None

    return AnomalyTrainingResult(
        model_id=model_id,
        model_name=model_name,
        model_version=resolved_version,
        model_type=MODEL_TYPE,
        feature_schema_version=dataset.feature_schema_version,
        dataset_schema=dataset_schema,
        training_sample_count=len(X_train),
        validation_sample_count=len(X_val),
        feature_count=len(FEATURE_SCHEMA),
        excluded_non_benign_count=excluded_non_benign,
        excluded_unmappable_label_count=dataset.excluded_unmappable_label_count,
        training_config=training_config,
        preprocessing_config=preprocessing_config,
        random_seed=RANDOM_SEED,
        validation_normal_rate=validation_normal_rate,
        artifact_path=str(artifact_path),
    )


def _select_benign_rows(dataset: TrainingDataset) -> tuple[list[list[float | None]], int]:
    normal_rows = [
        row for row, label in zip(dataset.feature_matrix, dataset.labels) if int(label) == int(BinaryLabel.BENIGN)
    ]
    excluded_non_benign = len(dataset.feature_matrix) - len(normal_rows)
    return normal_rows, excluded_non_benign


def _validate_sample_count(normal_count: int) -> None:
    if normal_count < MIN_NORMAL_SAMPLES:
        raise AnomalyTrainingError(
            "Insufficient normal-traffic (BENIGN) samples to train an anomaly detector: need at least "
            f"{MIN_NORMAL_SAMPLES}, found {normal_count}."
        )


def _validate_single_dataset_schema(dataset: TrainingDataset) -> str:
    distinct_schemas = set(dataset.dataset_schemas)
    if len(distinct_schemas) != 1:
        raise AnomalyTrainingError(
            "Training data spans more than one dataset schema "
            f"({sorted(distinct_schemas)}); train one anomaly detector per dataset family."
        )
    return next(iter(distinct_schemas))


def _remove_orphaned_artifact(artifact_path: Path) -> None:
    try:
        artifact_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove orphaned anomaly-detector artifact %s", artifact_path.name)


__all__ = [
    "AnomalyTrainingError",
    "AnomalyTrainingResult",
    "train_anomaly_detector",
    "RANDOM_SEED",
    "VALIDATION_FRACTION",
    "MODEL_TYPE",
]
