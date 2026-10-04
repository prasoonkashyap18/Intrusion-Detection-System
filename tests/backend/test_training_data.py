"""Tests for app.services.training_data: persisted MappedFeatureRecord rows
-> a deterministic feature matrix + binary labels for training. No test
here trains a model — that is app.services.model_training's job.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.models.mapped_feature_record import MappedFeatureRecord
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.training_data import ModelTrainingError, load_training_data


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_feature_row(
    db_session, batch_id, row_number=1, *, label="normal", dataset_schema="nsl-kdd-style", schema_version=None, present_value=1.0, distinct_values=False
):
    features = {
        name: {
            "value": float(index) if distinct_values else present_value,
            "status": "present",
            "source_column": "x",
            "raw_value": str(present_value),
        }
        for index, name in enumerate(FEATURE_SCHEMA)
    }
    row = MappedFeatureRecord(
        batch_id=batch_id,
        row_number=row_number,
        feature_schema_version=schema_version or FEATURE_SCHEMA_VERSION,
        dataset_schema=dataset_schema,
        features=features,
        unknown_fields={},
        label=label,
    )
    db_session.add(row)
    db_session.commit()
    return row


class TestLoadingPersistedFeatures:
    def test_loads_rows_for_the_given_batch(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, row_number=1)
        add_feature_row(db_session, batch.id, row_number=2, label="neptune")

        dataset = load_training_data(db_session, [batch.id])

        assert len(dataset.feature_matrix) == 2
        assert len(dataset.labels) == 2

    def test_never_re_reads_a_csv_only_the_database(self, db_session):
        # No upload_dir, no filesystem access anywhere in this test — if
        # load_training_data touched the filesystem it would raise.
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id)

        load_training_data(db_session, [batch.id])  # must not raise


class TestDeterministicFeatureOrdering:
    def test_feature_vector_rows_follow_feature_schema_order(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, present_value=42.0)

        dataset = load_training_data(db_session, [batch.id])

        assert len(dataset.feature_matrix[0]) == len(FEATURE_SCHEMA)
        assert all(v == 42.0 for v in dataset.feature_matrix[0])

    def test_feature_vector_positions_match_feature_schema_exactly(self, db_session):
        # Each feature holds its own FEATURE_SCHEMA index as its value, so
        # any reordering (not just a wrong count or a shared constant)
        # is detected.
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, distinct_values=True)

        dataset = load_training_data(db_session, [batch.id])

        assert dataset.feature_matrix[0] == [float(i) for i in range(len(FEATURE_SCHEMA))]


class TestLabelExtraction:
    def test_benign_label_maps_to_zero(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, label="normal")

        dataset = load_training_data(db_session, [batch.id])

        assert dataset.labels == [BinaryLabel.BENIGN]

    def test_attack_label_maps_to_one(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, label="neptune")

        dataset = load_training_data(db_session, [batch.id])

        assert dataset.labels == [BinaryLabel.ATTACK]


class TestMissingLabels:
    def test_a_row_with_no_label_is_excluded_not_defaulted_to_benign(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, label=None)

        dataset = load_training_data(db_session, [batch.id])

        assert len(dataset.labels) == 0
        assert dataset.excluded_unmappable_label_count == 1


class TestUnsupportedLabels:
    def test_a_label_not_matching_its_dataset_convention_is_excluded(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, label="maybe", dataset_schema="unsw-nb15-style")

        dataset = load_training_data(db_session, [batch.id])

        assert dataset.excluded_unmappable_label_count == 1
        assert len(dataset.labels) == 0

    def test_an_unrecognized_dataset_schema_excludes_every_row(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, dataset_schema="future-dataset-style", label="whatever")

        dataset = load_training_data(db_session, [batch.id])

        assert dataset.excluded_unmappable_label_count == 1


class TestFeatureLabelSeparation:
    def test_label_text_never_appears_inside_the_feature_matrix(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, label="neptune", present_value=7.0)

        dataset = load_training_data(db_session, [batch.id])

        assert "neptune" not in dataset.feature_matrix[0]
        assert all(isinstance(v, float) or v is None for v in dataset.feature_matrix[0])


class TestFeatureSchemaCompatibility:
    def test_a_mismatched_schema_version_raises(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, schema_version="999")

        with pytest.raises(ModelTrainingError):
            load_training_data(db_session, [batch.id])

    def test_a_matching_schema_version_loads_cleanly(self, db_session):
        batch = add_batch(db_session)
        add_feature_row(db_session, batch.id, schema_version=FEATURE_SCHEMA_VERSION)

        dataset = load_training_data(db_session, [batch.id])

        assert dataset.feature_schema_version == FEATURE_SCHEMA_VERSION


class TestEmptyTrainingSet:
    def test_an_empty_batch_list_returns_an_empty_dataset_not_an_error(self, db_session):
        dataset = load_training_data(db_session, [])

        assert dataset.feature_matrix == []
        assert dataset.labels == []

    def test_a_batch_with_no_persisted_rows_returns_empty(self, db_session):
        batch = add_batch(db_session)

        dataset = load_training_data(db_session, [batch.id])

        assert dataset.feature_matrix == []


class TestMultipleBatchesAndIsolation:
    def test_loading_two_batches_combines_their_rows(self, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        add_feature_row(db_session, batch_a.id, label="normal")
        add_feature_row(db_session, batch_b.id, label="neptune")

        dataset = load_training_data(db_session, [batch_a.id, batch_b.id])

        assert len(dataset.feature_matrix) == 2
        assert set(dataset.batch_ids) == {batch_a.id, batch_b.id}

    def test_a_batch_not_included_in_the_request_contributes_nothing(self, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        add_feature_row(db_session, batch_a.id)
        add_feature_row(db_session, batch_b.id)

        dataset = load_training_data(db_session, [batch_a.id])

        assert len(dataset.feature_matrix) == 1
        assert dataset.batch_ids == [batch_a.id]


class TestNoMetadataLeakageIntoFeatureMatrix:
    def test_record_ids_and_batch_ids_are_not_feature_values(self, db_session):
        batch = add_batch(db_session)
        row = add_feature_row(db_session, batch.id, present_value=3.0)

        dataset = load_training_data(db_session, [batch.id])

        assert str(row.id) not in dataset.feature_matrix[0]
        assert str(batch.id) not in dataset.feature_matrix[0]
