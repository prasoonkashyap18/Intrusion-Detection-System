"""Training-data loading: persisted `MappedFeatureRecord` rows (Step 21) ->
a deterministic feature matrix + binary labels, ready for
`app.services.model_training`.

    Database (MappedFeatureRecord rows)
        |
    load_training_data()
        |
    TrainingDataset   <- FEATURE_SCHEMA-ordered feature matrix, binary
                          labels, no label/metadata leakage

This module's only data source is already-persisted `MappedFeatureRecord`
rows, read through `app.services.feature_persistence.load_feature_vector`
(Step 21) — it never re-reads a batch's original CSV, and never re-runs
ingestion, dataset adaptation, feature extraction, or feature mapping.

Label handling: a row's binary training label is derived from its stored
`dataset_schema` + `label` via `app.services.dataset_adapters.
label_mapping.to_binary_label` — the one place dataset-specific label
interpretation lives (see that module's docstring). A row whose label is
missing, or does not match its dataset's documented convention, is
excluded from the returned dataset rather than defaulted to benign or
guessed — `TrainingDataset.excluded_unmappable_label_count` reports how
many.

Schema compatibility: every loaded row's `feature_schema_version` must
match the code's current `FEATURE_SCHEMA_VERSION`. A single mismatched row
raises `ModelTrainingError` immediately — mixing feature-schema versions
in one training run would silently corrupt what each feature column means,
so this is a hard failure, not a row to quietly skip.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mapped_feature_record import MappedFeatureRecord
from app.services.dataset_adapters.label_mapping import BinaryLabel, to_binary_label
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.feature_persistence import load_feature_vector


class ModelTrainingError(Exception):
    """Raised for any training precondition this layer cannot safely meet
    on its own — never silently worked around. `message` is written to be
    safe to surface to a caller: no filesystem paths, no stack traces, no
    SQL."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class TrainingDataset:
    """The prepared, in-memory training inputs for one training run. Not
    persisted anywhere by this module."""

    feature_matrix: list[list[float | None]]
    """One row per included `MappedFeatureRecord`, each a `FEATURE_SCHEMA`
    -ordered list of values. `None` means genuinely missing/malformed/
    unsupported for that feature — never replaced with `0` here; see
    `app.services.model_training` for where imputation (fit on the
    training split only) turns this into model input."""

    labels: list[BinaryLabel]
    """Parallel to `feature_matrix`: each row's binary training target."""

    record_ids: list[uuid.UUID]
    """Parallel to `feature_matrix`: which `MappedFeatureRecord.id`
    produced each row — traceability only, never itself a feature."""

    batch_ids: list[uuid.UUID]
    """Parallel to `feature_matrix`: which batch each row came from."""

    dataset_schemas: list[str]
    """Parallel to `feature_matrix`: which dataset adapter's schema
    produced each row. `app.services.model_training` requires these all be
    the same before training — mixing conventions in one model is a
    modeling decision outside this step's scope, not something to allow
    silently."""

    feature_schema_version: str
    excluded_unmappable_label_count: int
    """Rows loaded from the given batches whose label could not be mapped
    to BENIGN/ATTACK (missing, or not matching their dataset's documented
    convention) and were therefore excluded — never defaulted to benign."""


def load_training_data(db: Session, batch_ids: Sequence[uuid.UUID]) -> TrainingDataset:
    """Loads every `MappedFeatureRecord` row for `batch_ids`, in `id` order
    (a stable, deterministic order independent of insertion timing).

    Raises `ModelTrainingError` if any loaded row's `feature_schema_version`
    does not match the code's current `FEATURE_SCHEMA_VERSION`. Does not
    raise for an empty result, or for every row's label being unmappable —
    `app.services.model_training` decides whether what came back is enough
    to train on.
    """
    rows = list(
        db.scalars(
            select(MappedFeatureRecord)
            .where(MappedFeatureRecord.batch_id.in_(batch_ids))
            .order_by(MappedFeatureRecord.batch_id, MappedFeatureRecord.row_number)
        )
    )

    feature_matrix: list[list[float | None]] = []
    labels: list[BinaryLabel] = []
    record_ids: list[uuid.UUID] = []
    loaded_batch_ids: list[uuid.UUID] = []
    dataset_schemas: list[str] = []
    excluded_unmappable = 0

    for row in rows:
        if row.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ModelTrainingError(
                f"Persisted feature row(s) use schema version {row.feature_schema_version!r}, "
                f"but the current feature schema is version {FEATURE_SCHEMA_VERSION!r}. "
                "Re-process the affected batch(es) before training."
            )

        binary_label = to_binary_label(row.dataset_schema, row.label)
        if binary_label is None:
            excluded_unmappable += 1
            continue

        feature_matrix.append(load_feature_vector(row))
        labels.append(binary_label)
        record_ids.append(row.id)
        loaded_batch_ids.append(row.batch_id)
        dataset_schemas.append(row.dataset_schema)

    return TrainingDataset(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=loaded_batch_ids,
        dataset_schemas=dataset_schemas,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=excluded_unmappable,
    )


__all__ = ["ModelTrainingError", "TrainingDataset", "load_training_data", "FEATURE_SCHEMA"]
