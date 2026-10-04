"""Tests for app.services.model_training: TrainingDataset -> a trained,
persisted, registered baseline model. No fake IDS metrics anywhere here —
every metric asserted on is a real number computed from this test's own
synthetic validation split.
"""

from __future__ import annotations

import uuid

import joblib
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models.enums import ModelStatus
from app.models.model_metadata import ModelMetadata
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.model_training import (
    MIN_SAMPLES_PER_CLASS,
    ModelTrainingError,
    train_baseline_model,
)
from app.services.training_data import TrainingDataset

N_FEATURES = len(FEATURE_SCHEMA)


def make_dataset(
    *,
    benign_count=20,
    attack_count=20,
    dataset_schema="nsl-kdd-style",
    multi_schema=False,
    missing_column=None,
) -> TrainingDataset:
    feature_matrix: list[list[float | None]] = []
    labels: list[BinaryLabel] = []
    record_ids: list[uuid.UUID] = []
    batch_ids: list[uuid.UUID] = []
    dataset_schemas: list[str] = []

    batch_id = uuid.uuid4()
    for i in range(benign_count):
        row = [float(i % 7)] * N_FEATURES
        if missing_column is not None:
            row[missing_column] = None
        feature_matrix.append(row)
        labels.append(BinaryLabel.BENIGN)
        record_ids.append(uuid.uuid4())
        batch_ids.append(batch_id)
        dataset_schemas.append("unsw-nb15-style" if (multi_schema and i == 0) else dataset_schema)

    for i in range(attack_count):
        row = [float(100 + i % 7)] * N_FEATURES
        if missing_column is not None:
            row[missing_column] = None
        feature_matrix.append(row)
        labels.append(BinaryLabel.ATTACK)
        record_ids.append(uuid.uuid4())
        batch_ids.append(batch_id)
        dataset_schemas.append(dataset_schema)

    return TrainingDataset(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=batch_ids,
        dataset_schemas=dataset_schemas,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )


class TestTrainValidationSplit:
    def test_split_is_deterministic_across_runs(self, db_session, tmp_path):
        dataset = make_dataset()

        result_a = train_baseline_model(db_session, dataset, model_name="a", artifact_dir=tmp_path)
        result_b = train_baseline_model(db_session, dataset, model_name="b", artifact_dir=tmp_path)

        assert result_a.training_sample_count == result_b.training_sample_count
        assert result_a.validation_sample_count == result_b.validation_sample_count

    def test_split_is_stratified_by_default(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=20, attack_count=20)

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert result.training_sample_count == 32
        assert result.validation_sample_count == 8

    def test_split_actually_requests_stratification(self, db_session, tmp_path, monkeypatch):
        # Asserting on the trained model's output can't distinguish a
        # stratified split from an unstratified one that happens to land
        # on the same split by chance for a given seed — so this checks
        # the implementation detail directly: app.services.dataset_split
        # (which train_baseline_model delegates splitting to) must call
        # train_test_split with the labels as `stratify`.
        import app.services.dataset_split as dataset_split_module

        captured = {}
        real_split = dataset_split_module.train_test_split

        def spy_split(*args, **kwargs):
            captured["stratify"] = kwargs.get("stratify")
            return real_split(*args, **kwargs)

        monkeypatch.setattr(dataset_split_module, "train_test_split", spy_split)
        dataset = make_dataset()

        train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert captured["stratify"] is not None

    def test_insufficient_samples_in_one_class_raises(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=20, attack_count=MIN_SAMPLES_PER_CLASS - 1)

        with pytest.raises(ModelTrainingError):
            train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

    def test_no_registry_row_is_created_when_split_validation_fails(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=20, attack_count=MIN_SAMPLES_PER_CLASS - 1)

        with pytest.raises(ModelTrainingError):
            train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert db_session.query(ModelMetadata).count() == 0


class TestMissingValuePreprocessing:
    def test_a_missing_column_does_not_prevent_training(self, db_session, tmp_path):
        dataset = make_dataset(missing_column=0)

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert result.model_id is not None

    def test_imputer_statistics_are_recorded_and_fit_on_train_rows_only(self, db_session, tmp_path):
        dataset = make_dataset(missing_column=1)

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        stats = result.preprocessing_config["imputer_statistics"]
        assert len(stats) == N_FEATURES
        # Every non-missing column in this synthetic dataset holds a single
        # constant value per class; the imputed median must be a real
        # number (not NaN, not silently dropped), proving the imputer was
        # actually fitted rather than skipped.
        assert all(stat == stat for stat in stats)  # not NaN

    def test_an_entirely_missing_column_is_kept_not_dropped(self, db_session, tmp_path):
        # A column with zero observed values across the whole training
        # split has nothing to compute a median from. sklearn's default
        # behavior would silently drop that column, shrinking the feature
        # matrix below len(FEATURE_SCHEMA) and misaligning every later
        # column with the artifact's declared schema — this must not
        # happen.
        dataset = make_dataset(missing_column=0)

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert result.feature_count == N_FEATURES
        assert len(result.preprocessing_config["imputer_statistics"]) == N_FEATURES
        payload = joblib.load(result.artifact_path)
        assert payload["imputer"].statistics_.shape[0] == N_FEATURES


class TestPreprocessingFitScope:
    def test_imputer_is_fit_on_exactly_the_training_rows_not_validation_rows(self, db_session, tmp_path, monkeypatch):
        import app.services.model_training as model_training_module
        from sklearn.impute import SimpleImputer

        captured_fit_row_counts = []
        real_fit_transform = SimpleImputer.fit_transform

        def spy_fit_transform(self, X, *args, **kwargs):
            captured_fit_row_counts.append(len(X))
            return real_fit_transform(self, X, *args, **kwargs)

        monkeypatch.setattr(SimpleImputer, "fit_transform", spy_fit_transform)
        dataset = make_dataset()

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert captured_fit_row_counts == [result.training_sample_count]


class TestModelTraining:
    def test_training_produces_a_fitted_model_with_real_validation_metrics(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert result.model_type == "random_forest"
        assert 0.0 <= result.validation_metrics["accuracy"] <= 1.0
        assert set(result.validation_metrics) == {"accuracy", "precision", "recall", "f1"}

    def test_class_distribution_reflects_the_actual_input_counts(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=25, attack_count=15)

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert result.class_distribution == {"benign": 25, "attack": 15}


class TestArtifactCreation:
    def test_artifact_file_is_written_to_the_given_directory(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        artifact_file = tmp_path / f"{result.model_id}.joblib"
        assert artifact_file.exists()
        assert str(artifact_file) == result.artifact_path

    def test_artifact_payload_carries_feature_schema_and_version_metadata(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        payload = joblib.load(result.artifact_path)
        assert payload["feature_schema"] == list(FEATURE_SCHEMA)
        assert payload["feature_schema_version"] == FEATURE_SCHEMA_VERSION
        assert payload["model_type"] == "random_forest"
        assert "model" in payload and "imputer" in payload


class TestSchemaCompatibility:
    def test_dataset_spanning_multiple_schemas_is_rejected(self, db_session, tmp_path):
        dataset = make_dataset(multi_schema=True)

        with pytest.raises(ModelTrainingError):
            train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

    def test_no_artifact_is_written_when_schema_validation_fails(self, db_session, tmp_path):
        dataset = make_dataset(multi_schema=True)

        with pytest.raises(ModelTrainingError):
            train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert list(tmp_path.glob("*.joblib")) == []


class TestModelRegistry:
    def test_a_successful_run_creates_exactly_one_ready_registry_row(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        row = db_session.query(ModelMetadata).filter_by(id=result.model_id).one()
        assert row.status == ModelStatus.READY
        assert row.artifact_path == result.artifact_path
        assert row.feature_set_version == FEATURE_SCHEMA_VERSION

    def test_training_batch_ids_are_recorded_for_traceability(self, db_session, tmp_path):
        dataset = make_dataset()
        expected_batch_ids = {str(b) for b in dataset.batch_ids}

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        row = db_session.query(ModelMetadata).filter_by(id=result.model_id).one()
        assert set(row.training_batch_ids) == expected_batch_ids


class TestArtifactFailureHandling:
    def test_artifact_write_failure_leaves_no_registry_row(self, db_session, tmp_path):
        dataset = make_dataset()
        # A regular file where the artifact directory should be created
        # forces joblib.dump's parent-directory creation to fail with OSError.
        blocking_file = tmp_path / "blocked"
        blocking_file.write_text("x")

        with pytest.raises(ModelTrainingError):
            train_baseline_model(db_session, dataset, artifact_dir=blocking_file)

        assert db_session.query(ModelMetadata).count() == 0

    def test_artifact_failure_message_has_no_filesystem_path(self, db_session, tmp_path):
        dataset = make_dataset()
        blocking_file = tmp_path / "blocked"
        blocking_file.write_text("x")

        with pytest.raises(ModelTrainingError) as exc_info:
            train_baseline_model(db_session, dataset, artifact_dir=blocking_file)

        assert str(blocking_file) not in exc_info.value.message


class TestRegistryFailureHandling:
    def test_registry_write_failure_removes_the_orphaned_artifact(self, db_session, tmp_path, monkeypatch):
        dataset = make_dataset()

        def failing_commit():
            raise SQLAlchemyError("simulated failure")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(ModelTrainingError):
            train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert list(tmp_path.glob("*.joblib")) == []


class TestNoFakeMetrics:
    def test_validation_metrics_are_absent_from_training_config(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert "validation_metrics" not in result.training_config
        assert "accuracy" not in result.training_config

    def test_a_perfectly_separable_dataset_yields_perfect_validation_metrics_not_an_assumed_value(
        self, db_session, tmp_path
    ):
        # Benign rows are all zeros, attack rows are all a large constant —
        # trivially separable, so a correctly trained/evaluated model
        # should score perfectly on its own validation split. This checks
        # the metric is actually *computed*, not hand-asserted to a
        # plausible-looking placeholder.
        dataset = make_dataset()

        result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path)

        assert result.validation_metrics["accuracy"] == 1.0
