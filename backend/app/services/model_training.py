"""Baseline model training: `TrainingDataset` -> a trained, persisted,
registered classifier.

    TrainingDataset (app.services.training_data)
        |
    prepare_training_data() (app.services.dataset_split) -> train/validation/test
        |
    fit imputer on the TRAIN partition only
        |
    train RandomForestClassifier (fixed, documented configuration)
        |
    evaluate on the VALIDATION partition (real predictions only)
        |
    write artifact (joblib) -> register in ModelMetadata (only after the
        artifact write succeeds)
        |
    TrainingResult

This module is the training FOUNDATION, not the runtime detection engine:
it does not expose a prediction API, does not create `DetectionResult`
rows, and does not compute or display any attack confidence, severity,
risk, or threat score. `validation_metrics` on the result are real numbers
computed from this run's own held-out validation partition — never a
production-performance claim, and never shown anywhere outside this
internal result object in this step.

Splitting itself is not implemented here — `app.services.dataset_split.
prepare_training_data` owns that (train/validation/test partitioning,
stratification, and batch/group isolation), so this module never
duplicates split logic. By default this module requests `test_fraction=0.0`
(no held-out test partition) and `group_by_batch=False`, which reproduces
this module's original train/validation-only behavior exactly; a caller
may opt into a genuine held-out `test` partition and/or batch-isolated
splitting via the `test_fraction`/`group_by_batch` keyword arguments.
Whatever `test` partition is produced is deliberately never read by this
module — evaluating against it is a later step's job, not this one's.

No dataset-specific logic lives here: label interpretation is
`app.services.dataset_adapters.label_mapping`'s job, already applied by
the time `TrainingDataset` reaches this module.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.enums import ModelStatus
from app.models.model_metadata import ModelMetadata
from app.services.dataset_split import DatasetSplitError, prepare_training_data
from app.services.feature_extraction import FEATURE_SCHEMA
from app.services.training_data import ModelTrainingError, TrainingDataset

logger = logging.getLogger("ai_ids")

MODEL_TYPE = "random_forest"
RANDOM_SEED = 42
VALIDATION_FRACTION = 0.2
MIN_SAMPLES_PER_CLASS = 5
"""A stratified split needs enough of each class that `VALIDATION_FRACTION`
still leaves at least one member of every class in *both* the training and
validation split after rounding (sklearn's stratified `train_test_split`
raises otherwise). 5 per class comfortably clears that bar at the 20%
validation fraction this module uses."""

RANDOM_FOREST_PARAMS: dict[str, object] = {
    "n_estimators": 100,
    "max_depth": None,
    "random_state": RANDOM_SEED,
    "class_weight": None,
}
"""Fixed and explicit on purpose — this step trains one deterministic
baseline configuration, not a hyperparameter search."""

IMPUTATION_STRATEGY = "median"
"""Per-feature median, fitted on the training split only. `keep_empty_
features=True` is required below: without it, SimpleImputer silently
*drops* a column that is entirely missing across the whole training split
(no median to compute), shrinking the feature matrix below
`len(FEATURE_SCHEMA)` and misaligning every later column with the
artifact's declared schema. With it, such a column is kept and filled with
0.0 — a known, documented edge case, not a general "replace missing with
zero" policy; see backend/README.md."""


@dataclass(frozen=True)
class TrainingResult:
    """Truthful metadata about one completed training run. Every field is
    either an actual count/configuration value or a metric computed from
    this run's own real validation predictions — nothing here is invented.
    """

    model_id: uuid.UUID
    model_name: str
    model_version: str
    model_type: str
    feature_schema_version: str
    dataset_schema: str
    training_sample_count: int
    validation_sample_count: int
    test_sample_count: int
    """The size of the held-out test partition `app.services.dataset_split`
    prepared alongside train/validation — `0` whenever `test_fraction=0.0`
    (this module's default). Reported for transparency only; this module
    never reads the test partition's contents or evaluates against it."""
    feature_count: int
    class_distribution: dict[str, int]
    """Benign/attack counts across every row actually used (train + validation
    combined) — never including rows excluded for an unmappable label."""
    excluded_unmappable_label_count: int
    training_config: dict = field(default_factory=dict)
    preprocessing_config: dict = field(default_factory=dict)
    random_seed: int = RANDOM_SEED
    validation_metrics: dict = field(default_factory=dict)
    """Computed only from the held-out validation split's real predictions
    — see module docstring. Never a production/runtime performance claim."""
    artifact_path: str = ""


def train_baseline_model(
    db: Session,
    dataset: TrainingDataset,
    *,
    model_name: str = "ai-ids-baseline",
    model_version: str | None = None,
    artifact_dir: Path,
    test_fraction: float = 0.0,
    group_by_batch: bool = False,
) -> TrainingResult:
    """Trains, evaluates, persists, and registers one baseline model from
    an already-loaded `TrainingDataset` (see `app.services.training_data.
    load_training_data`).

    Splitting is delegated to `app.services.dataset_split.
    prepare_training_data` (see that module for the full split strategy).
    `test_fraction=0.0` and `group_by_batch=False` are this function's
    defaults, reproducing its original train/validation-only behavior; the
    prepared `test` partition (when `test_fraction > 0`) is never read by
    this function — it is reserved for a later evaluation step.

    Raises `ModelTrainingError` for any precondition this function cannot
    safely proceed past: too few samples, an unsplittable dataset (wrapped
    from `DatasetSplitError`), or training rows spanning more than one
    dataset schema (a modeling decision outside this step's scope — train
    one model per dataset family). Raises `ModelTrainingError` (wrapping
    the original cause only in the server log, never in the message) if
    the artifact cannot be written, or if registering the trained model in
    the database fails — in the latter case, the orphaned artifact file is
    removed so a database failure never leaves an unregistered,
    undiscoverable model file masquerading as evidence of a successful
    run; see "Transaction safety" in backend/README.md.
    """
    _validate_sample_counts(dataset)
    dataset_schema = _validate_single_dataset_schema(dataset)

    try:
        prepared = prepare_training_data(
            dataset,
            test_fraction=test_fraction,
            validation_fraction=VALIDATION_FRACTION,
            random_seed=RANDOM_SEED,
            group_by_batch=group_by_batch,
        )
    except DatasetSplitError as exc:
        raise ModelTrainingError(exc.message) from None

    X_train = np.array(prepared.train.feature_matrix, dtype=float)
    y_train = np.array([int(label) for label in prepared.train.labels], dtype=int)
    X_val = np.array(prepared.validation.feature_matrix, dtype=float)
    y_val = np.array([int(label) for label in prepared.validation.labels], dtype=int)

    imputer = SimpleImputer(strategy=IMPUTATION_STRATEGY, keep_empty_features=True)
    X_train_imputed = imputer.fit_transform(X_train)  # fitted on TRAIN ONLY
    X_val_imputed = imputer.transform(X_val)  # validation never influences the fit

    model = RandomForestClassifier(**RANDOM_FOREST_PARAMS)
    model.fit(X_train_imputed, y_train)

    predictions = model.predict(X_val_imputed)
    validation_metrics = {
        "accuracy": float(accuracy_score(y_val, predictions)),
        "precision": float(precision_score(y_val, predictions, zero_division=0)),
        "recall": float(recall_score(y_val, predictions, zero_division=0)),
        "f1": float(f1_score(y_val, predictions, zero_division=0)),
    }

    model_id = uuid.uuid4()
    resolved_version = model_version or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_path = artifact_dir / f"{model_id}.joblib"

    training_config = {
        "validation_fraction": VALIDATION_FRACTION,
        "test_fraction": test_fraction,
        "group_by_batch": group_by_batch,
        "random_seed": RANDOM_SEED,
        "model_type": MODEL_TYPE,
        "model_params": dict(RANDOM_FOREST_PARAMS),
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
        logger.exception("Failed to write model artifact for model %s", model_id)
        raise ModelTrainingError("Unable to save the trained model artifact.") from None

    class_distribution = {
        "benign": prepared.train.class_distribution["benign"] + prepared.validation.class_distribution["benign"],
        "attack": prepared.train.class_distribution["attack"] + prepared.validation.class_distribution["attack"],
    }

    try:
        registry_entry = ModelMetadata(
            id=model_id,
            model_name=model_name,
            model_version=resolved_version,
            model_type=MODEL_TYPE,
            dataset_name=dataset_schema,
            feature_set_version=dataset.feature_schema_version,
            trained_at=datetime.now(timezone.utc),
            evaluation_metrics=validation_metrics,
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
        logger.exception("Failed to register trained model %s; removing orphaned artifact", model_id)
        _remove_orphaned_artifact(artifact_path)
        raise ModelTrainingError("Unable to register the trained model.") from None

    return TrainingResult(
        model_id=model_id,
        model_name=model_name,
        model_version=resolved_version,
        model_type=MODEL_TYPE,
        feature_schema_version=dataset.feature_schema_version,
        dataset_schema=dataset_schema,
        training_sample_count=len(y_train),
        validation_sample_count=len(y_val),
        test_sample_count=prepared.test.sample_count,
        feature_count=len(FEATURE_SCHEMA),
        class_distribution=class_distribution,
        excluded_unmappable_label_count=dataset.excluded_unmappable_label_count,
        training_config=training_config,
        preprocessing_config=preprocessing_config,
        random_seed=RANDOM_SEED,
        validation_metrics=validation_metrics,
        artifact_path=str(artifact_path),
    )


def _validate_sample_counts(dataset: TrainingDataset) -> None:
    if len(dataset.labels) == 0:
        raise ModelTrainingError("No labeled training data is available for the requested batch(es).")

    benign_count = sum(1 for label in dataset.labels if int(label) == 0)
    attack_count = sum(1 for label in dataset.labels if int(label) == 1)
    if benign_count < MIN_SAMPLES_PER_CLASS or attack_count < MIN_SAMPLES_PER_CLASS:
        raise ModelTrainingError(
            "Insufficient labeled samples to train: need at least "
            f"{MIN_SAMPLES_PER_CLASS} benign and {MIN_SAMPLES_PER_CLASS} attack samples, "
            f"found {benign_count} benign and {attack_count} attack."
        )


def _validate_single_dataset_schema(dataset: TrainingDataset) -> str:
    distinct_schemas = set(dataset.dataset_schemas)
    if len(distinct_schemas) != 1:
        raise ModelTrainingError(
            "Training data spans more than one dataset schema "
            f"({sorted(distinct_schemas)}); train one model per dataset family."
        )
    return next(iter(distinct_schemas))


def _remove_orphaned_artifact(artifact_path: Path) -> None:
    try:
        artifact_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove orphaned model artifact %s", artifact_path.name)


__all__ = ["TrainingResult", "train_baseline_model", "RANDOM_SEED", "VALIDATION_FRACTION", "MODEL_TYPE"]
