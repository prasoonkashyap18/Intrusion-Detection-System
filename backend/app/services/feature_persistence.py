"""Persists `app.services.feature_mapping.MappedFeatureSet` rows so a later
ML/detection step can read a batch's mapped features back without
re-reading and re-parsing its original CSV.

This module performs NO machine learning: it does not predict, classify,
score, or create a `DetectionResult` row — it only writes
`MappedFeatureRecord` rows (`app.models.mapped_feature_record`), exactly
the content `app.services.feature_mapping` already computed, into the
database. It has no knowledge of any specific dataset family — it only
knows the `MappedFeatureSet` contract, never a dataset's own column names.

Transaction boundary: `persist_mapped_features` only `db.add()`s — it does
not commit. The caller (`app.services.batch_processor`) controls the
transaction boundary, so a whole batch's worth of persisted rows commits or
rolls back together with the batch's resulting status, never partially.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.mapped_feature_record import MappedFeatureRecord
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.feature_mapping import MappedFeatureSet


def persist_mapped_features(db: Session, batch_id: uuid.UUID, mapped: MappedFeatureSet) -> MappedFeatureRecord:
    """Stages one row's mapped features for insertion — never fabricates a
    value, and never stores `label`/`attack_category` (neither exists on
    `MappedFeatureSet` to begin with). Does not commit; see the module
    docstring for why.
    """
    record = MappedFeatureRecord(
        batch_id=batch_id,
        row_number=mapped.row_number,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        dataset_schema=mapped.dataset_schema,
        features={
            name: {
                "value": mapped.features[name].value,
                "status": mapped.features[name].status.value,
                "source_column": mapped.features[name].source_column,
                "raw_value": mapped.features[name].raw_value,
            }
            for name in FEATURE_SCHEMA
        },
        unknown_fields=dict(mapped.unknown_fields),
    )
    db.add(record)
    return record


def load_feature_vector(record: MappedFeatureRecord) -> list[float | int | None]:
    """Rebuilds one persisted row's ordered feature vector from its stored
    `features` JSON, walking `FEATURE_SCHEMA` explicitly rather than
    trusting JSON/dict key order — the same discipline `app.services.
    feature_mapping.MappedFeatureSet.feature_vector()` already applies
    before persistence, now applied again after a round trip through the
    database.
    """
    return [record.features[name]["value"] for name in FEATURE_SCHEMA]
