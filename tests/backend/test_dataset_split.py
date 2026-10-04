"""Tests for app.services.dataset_split: TrainingDataset ->
train/validation/test partitions, deterministic and leakage-free.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.dataset_split import (
    DatasetSplitError,
    prepare_training_data,
)
from app.services.training_data import TrainingDataset

N_FEATURES = 5


def make_dataset(
    *,
    benign_count=60,
    attack_count=60,
    n_batches=6,
    feature_width=N_FEATURES,
) -> TrainingDataset:
    """A synthetic dataset spread evenly across `n_batches` distinct
    batches, each batch internally a mix of benign/attack rows so that
    group-level splitting still has class diversity to work with.
    """
    feature_matrix: list[list[float | None]] = []
    labels: list[BinaryLabel] = []
    record_ids: list[uuid.UUID] = []
    batch_ids: list[uuid.UUID] = []

    batches = [uuid.uuid4() for _ in range(n_batches)]

    total = benign_count + attack_count
    benign_remaining = benign_count
    attack_remaining = attack_count
    for i in range(total):
        if benign_remaining > 0 and (attack_remaining == 0 or i % 2 == 0):
            label = BinaryLabel.BENIGN
            benign_remaining -= 1
        else:
            label = BinaryLabel.ATTACK
            attack_remaining -= 1
        row = [float(i)] * feature_width
        feature_matrix.append(row)
        labels.append(label)
        record_ids.append(uuid.uuid4())
        batch_ids.append(batches[i % n_batches])

    return TrainingDataset(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=batch_ids,
        dataset_schemas=["nsl-kdd-style"] * total,
        feature_schema_version="1",
        excluded_unmappable_label_count=0,
    )


class TestPartitionSizes:
    def test_three_partitions_roughly_match_requested_fractions(self):
        dataset = make_dataset()

        prepared = prepare_training_data(dataset, test_fraction=0.2, validation_fraction=0.2, group_by_batch=False)

        total = len(dataset.labels)
        assert prepared.train.sample_count + prepared.validation.sample_count + prepared.test.sample_count == total
        assert prepared.test.sample_count == pytest.approx(total * 0.2, abs=total * 0.05)
        assert prepared.validation.sample_count == pytest.approx(total * 0.2, abs=total * 0.05)

    def test_zero_test_fraction_produces_an_empty_test_partition(self):
        dataset = make_dataset()

        prepared = prepare_training_data(dataset, test_fraction=0.0, validation_fraction=0.2, group_by_batch=False)

        assert prepared.test.sample_count == 0
        assert prepared.test.feature_matrix == []
        assert prepared.train.sample_count + prepared.validation.sample_count == len(dataset.labels)


class TestDeterminism:
    def test_same_seed_produces_identical_partitions(self):
        dataset = make_dataset()

        a = prepare_training_data(dataset, random_seed=42, group_by_batch=False)
        b = prepare_training_data(dataset, random_seed=42, group_by_batch=False)

        assert a.train.record_ids == b.train.record_ids
        assert a.validation.record_ids == b.validation.record_ids
        assert a.test.record_ids == b.test.record_ids

    def test_same_seed_is_deterministic_in_group_mode_too(self):
        dataset = make_dataset()

        a = prepare_training_data(dataset, random_seed=7, group_by_batch=True)
        b = prepare_training_data(dataset, random_seed=7, group_by_batch=True)

        assert a.train.record_ids == b.train.record_ids
        assert a.test.record_ids == b.test.record_ids

    def test_different_seed_can_produce_different_partitions(self):
        dataset = make_dataset()

        a = prepare_training_data(dataset, random_seed=1, group_by_batch=False)
        b = prepare_training_data(dataset, random_seed=2, group_by_batch=False)

        assert a.test.record_ids != b.test.record_ids


class TestStratification:
    def test_row_level_split_preserves_roughly_equal_class_proportions(self):
        dataset = make_dataset(benign_count=60, attack_count=60)

        prepared = prepare_training_data(dataset, group_by_batch=False)

        for partition in (prepared.train, prepared.validation, prepared.test):
            assert partition.class_proportions["benign"] == pytest.approx(0.5, abs=0.1)

    def test_row_level_split_actually_requests_stratification(self, monkeypatch):
        # A balanced synthetic dataset can land close to proportional by
        # chance even without stratification, so this checks the
        # implementation detail directly: train_test_split must be called
        # with the labels as `stratify` for every row-level split stage.
        import app.services.dataset_split as dataset_split_module

        captured_stratify_values = []
        real_split = dataset_split_module.train_test_split

        def spy_split(*args, **kwargs):
            captured_stratify_values.append(kwargs.get("stratify"))
            return real_split(*args, **kwargs)

        monkeypatch.setattr(dataset_split_module, "train_test_split", spy_split)
        dataset = make_dataset()

        prepare_training_data(dataset, group_by_batch=False)

        assert len(captured_stratify_values) == 2  # test-carving stage + validation-carving stage
        assert all(value is not None for value in captured_stratify_values)

    def test_insufficient_samples_in_one_class_raises(self):
        dataset = make_dataset(benign_count=60, attack_count=5)

        with pytest.raises(DatasetSplitError):
            prepare_training_data(dataset, group_by_batch=False)

    def test_single_class_dataset_raises(self):
        dataset = make_dataset(benign_count=60, attack_count=0)

        with pytest.raises(DatasetSplitError):
            prepare_training_data(dataset, group_by_batch=False)


class TestGroupBatchIsolation:
    def test_no_batch_appears_in_more_than_one_partition(self):
        dataset = make_dataset(n_batches=6)

        prepared = prepare_training_data(dataset, group_by_batch=True)

        train_batches = set(prepared.train.batch_ids)
        val_batches = set(prepared.validation.batch_ids)
        test_batches = set(prepared.test.batch_ids)

        assert not (train_batches & val_batches)
        assert not (train_batches & test_batches)
        assert not (val_batches & test_batches)

    def test_group_mode_keeps_every_row_of_a_batch_together(self):
        dataset = make_dataset(n_batches=6)

        prepared = prepare_training_data(dataset, group_by_batch=True)

        # Every row from a given batch must land in the same partition as
        # every other row from that batch.
        batch_to_partition: dict[uuid.UUID, str] = {}
        for name, partition in (("train", prepared.train), ("validation", prepared.validation), ("test", prepared.test)):
            for batch_id in partition.batch_ids:
                assert batch_to_partition.get(batch_id, name) == name
                batch_to_partition[batch_id] = name

    def test_too_few_batches_for_group_mode_raises(self):
        dataset = make_dataset(n_batches=1)

        with pytest.raises(DatasetSplitError):
            prepare_training_data(dataset, group_by_batch=True)

    def test_group_by_batch_false_allows_a_single_batch(self):
        dataset = make_dataset(n_batches=1)

        prepared = prepare_training_data(dataset, group_by_batch=False)

        assert prepared.train.sample_count > 0


class TestNoLeakage:
    def test_no_record_id_appears_in_more_than_one_partition(self):
        dataset = make_dataset()

        prepared = prepare_training_data(dataset, group_by_batch=False)

        train_ids = set(prepared.train.record_ids)
        val_ids = set(prepared.validation.record_ids)
        test_ids = set(prepared.test.record_ids)

        assert not (train_ids & val_ids)
        assert not (train_ids & test_ids)
        assert not (val_ids & test_ids)
        assert len(train_ids) + len(val_ids) + len(test_ids) == len(dataset.labels)

    def test_every_original_record_id_is_accounted_for_exactly_once(self):
        dataset = make_dataset()

        prepared = prepare_training_data(dataset, group_by_batch=False)

        all_ids = set(prepared.train.record_ids) | set(prepared.validation.record_ids) | set(prepared.test.record_ids)
        assert all_ids == set(dataset.record_ids)

    def test_labels_never_appear_inside_feature_matrices(self):
        dataset = make_dataset()

        prepared = prepare_training_data(dataset, group_by_batch=False)

        for partition in (prepared.train, prepared.validation, prepared.test):
            for row in partition.feature_matrix:
                assert all(not isinstance(v, BinaryLabel) for v in row)

    def test_metadata_never_appears_inside_feature_matrices(self):
        dataset = make_dataset()

        prepared = prepare_training_data(dataset, group_by_batch=False)

        batch_id_strs = {str(b) for b in dataset.batch_ids}
        for partition in (prepared.train, prepared.validation, prepared.test):
            for row in partition.feature_matrix:
                assert not any(str(v) in batch_id_strs for v in row)


class TestPreprocessingIsolation:
    def test_prepare_training_data_never_returns_a_fitted_preprocessor(self):
        # This module's job is split indices only; no imputer/scaler is
        # ever part of its output, so there is nothing here that could be
        # fit on test/validation data by mistake.
        dataset = make_dataset()

        prepared = prepare_training_data(dataset, group_by_batch=False)

        assert not hasattr(prepared, "imputer")
        assert not hasattr(prepared.train, "imputer")


class TestMissingValueSemantics:
    def test_missing_values_stay_none_through_partitioning(self):
        dataset = make_dataset()
        # Introduce a missing value into one row.
        dataset.feature_matrix[0][0] = None

        prepared = prepare_training_data(dataset, group_by_batch=False)

        all_rows = prepared.train.feature_matrix + prepared.validation.feature_matrix + prepared.test.feature_matrix
        assert any(None in row for row in all_rows)

    def test_genuine_zero_is_preserved_not_treated_as_missing(self):
        dataset = make_dataset()
        dataset.feature_matrix[0][0] = 0.0

        prepared = prepare_training_data(dataset, group_by_batch=False)

        all_rows = prepared.train.feature_matrix + prepared.validation.feature_matrix + prepared.test.feature_matrix
        assert any(0.0 in row for row in all_rows)


class TestEmptyAndEdgeCases:
    def test_empty_dataset_raises(self):
        dataset = TrainingDataset(
            feature_matrix=[],
            labels=[],
            record_ids=[],
            batch_ids=[],
            dataset_schemas=[],
            feature_schema_version="1",
            excluded_unmappable_label_count=0,
        )

        with pytest.raises(DatasetSplitError):
            prepare_training_data(dataset)

    def test_excluded_unmappable_label_count_is_carried_through(self):
        dataset = make_dataset()
        dataset_with_exclusions = TrainingDataset(
            feature_matrix=dataset.feature_matrix,
            labels=dataset.labels,
            record_ids=dataset.record_ids,
            batch_ids=dataset.batch_ids,
            dataset_schemas=dataset.dataset_schemas,
            feature_schema_version=dataset.feature_schema_version,
            excluded_unmappable_label_count=7,
        )

        prepared = prepare_training_data(dataset_with_exclusions, group_by_batch=False)

        assert prepared.excluded_unmappable_label_count == 7


class TestMultipleBatches:
    def test_many_batches_still_splits_cleanly_in_group_mode(self):
        dataset = make_dataset(n_batches=10, benign_count=100, attack_count=100)

        prepared = prepare_training_data(dataset, group_by_batch=True)

        assert prepared.train.sample_count > 0
        assert prepared.validation.sample_count > 0
        assert prepared.test.sample_count > 0


class TestInvalidSplitConfiguration:
    def test_negative_test_fraction_raises(self):
        dataset = make_dataset()

        with pytest.raises(DatasetSplitError):
            prepare_training_data(dataset, test_fraction=-0.1)

    def test_validation_fraction_of_zero_raises(self):
        dataset = make_dataset()

        with pytest.raises(DatasetSplitError):
            prepare_training_data(dataset, validation_fraction=0.0)

    def test_fractions_summing_to_one_or_more_raises(self):
        dataset = make_dataset()

        with pytest.raises(DatasetSplitError):
            prepare_training_data(dataset, test_fraction=0.5, validation_fraction=0.5)

    def test_validation_fraction_of_one_raises(self):
        dataset = make_dataset()

        with pytest.raises(DatasetSplitError):
            prepare_training_data(dataset, validation_fraction=1.0)


class TestErrorMessagesAreSafe:
    def test_error_message_has_no_stack_trace_or_sql(self):
        dataset = make_dataset(benign_count=60, attack_count=5)

        with pytest.raises(DatasetSplitError) as exc_info:
            prepare_training_data(dataset, group_by_batch=False)

        message = exc_info.value.message
        assert "Traceback" not in message
        assert "SELECT" not in message.upper() or "select" not in message
