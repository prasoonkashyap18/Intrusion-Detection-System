"""Tests for app.services.anomaly_detection_training: TrainingDataset ->
a trained, persisted, registered IsolationForest anomaly detector, kept
entirely separate from the supervised classifier. No fake metrics here —
every number asserted on is real, computed from this test's own data.
"""

from __future__ import annotations

import uuid

import joblib
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models.enums import ModelStatus
from app.models.model_metadata import ModelMetadata
from app.services.anomaly_detection_training import (
    MIN_NORMAL_SAMPLES,
    AnomalyTrainingError,
    train_anomaly_detector,
)
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.training_data import TrainingDataset

N_FEATURES = len(FEATURE_SCHEMA)


def make_dataset(
    *,
    benign_count=40,
    attack_count=10,
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


class TestTrainsOnBenignOnly:
    def test_attack_rows_are_excluded_from_training(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=40, attack_count=10)

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert result.training_sample_count + result.validation_sample_count == 40
        assert result.excluded_non_benign_count == 10

    def test_training_plus_validation_never_exceeds_benign_count(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=50, attack_count=25)

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert result.training_sample_count + result.validation_sample_count == 50


class TestSampleCountValidation:
    def test_too_few_benign_samples_raises(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=MIN_NORMAL_SAMPLES - 1, attack_count=5)

        with pytest.raises(AnomalyTrainingError):
            train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

    def test_no_registry_row_is_created_when_sample_validation_fails(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=MIN_NORMAL_SAMPLES - 1, attack_count=5)

        with pytest.raises(AnomalyTrainingError):
            train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert db_session.query(ModelMetadata).count() == 0

    def test_a_dataset_with_zero_benign_rows_raises(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=0, attack_count=30)

        with pytest.raises(AnomalyTrainingError):
            train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)


class TestSchemaCompatibility:
    def test_dataset_spanning_multiple_schemas_is_rejected(self, db_session, tmp_path):
        dataset = make_dataset(multi_schema=True)

        with pytest.raises(AnomalyTrainingError):
            train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

    def test_no_artifact_is_written_when_schema_validation_fails(self, db_session, tmp_path):
        dataset = make_dataset(multi_schema=True)

        with pytest.raises(AnomalyTrainingError):
            train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert list(tmp_path.glob("*.joblib")) == []


class TestMissingValuePreprocessing:
    def test_a_missing_column_does_not_prevent_training(self, db_session, tmp_path):
        dataset = make_dataset(missing_column=0)

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert result.model_id is not None

    def test_an_entirely_missing_column_is_kept_not_dropped(self, db_session, tmp_path):
        dataset = make_dataset(missing_column=0)

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert len(result.preprocessing_config["imputer_statistics"]) == N_FEATURES
        payload = joblib.load(result.artifact_path)
        assert payload["imputer"].statistics_.shape[0] == N_FEATURES

    def test_imputer_is_fit_on_exactly_the_training_rows(self, db_session, tmp_path, monkeypatch):
        from sklearn.impute import SimpleImputer

        captured_fit_row_counts = []
        real_fit_transform = SimpleImputer.fit_transform

        def spy_fit_transform(self, X, *a, **k):
            captured_fit_row_counts.append(len(X))
            return real_fit_transform(self, X, *a, **k)

        monkeypatch.setattr(SimpleImputer, "fit_transform", spy_fit_transform)
        dataset = make_dataset(benign_count=40, attack_count=10)

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert captured_fit_row_counts == [result.training_sample_count]


class TestNormalAndAnomalousInputs:
    def test_detector_flags_a_far_outlier_as_anomalous(self, db_session, tmp_path):
        # Train on a tight, uniform "normal" cluster, then check the
        # fitted model's own prediction on a point wildly outside that
        # cluster — a real prediction from the real fitted model, not an
        # assumption about IsolationForest's behavior in general.
        dataset = make_dataset(benign_count=40, attack_count=0)

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)
        payload = joblib.load(result.artifact_path)

        import numpy as np

        normal_point = np.array([[3.0] * N_FEATURES])
        far_outlier = np.array([[100000.0] * N_FEATURES])

        normal_prediction = payload["model"].predict(payload["imputer"].transform(normal_point))
        outlier_prediction = payload["model"].predict(payload["imputer"].transform(far_outlier))

        assert outlier_prediction[0] == -1
        assert normal_prediction[0] == 1

    def test_validation_normal_rate_is_a_real_fraction_between_zero_and_one(self, db_session, tmp_path):
        dataset = make_dataset(benign_count=40, attack_count=0)

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert 0.0 <= result.validation_normal_rate <= 1.0


class TestArtifactCreation:
    def test_artifact_file_is_written_to_the_given_directory(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        artifact_file = tmp_path / f"{result.model_id}.joblib"
        assert artifact_file.exists()
        assert str(artifact_file) == result.artifact_path

    def test_artifact_payload_carries_feature_schema_and_version_metadata(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        payload = joblib.load(result.artifact_path)
        assert payload["feature_schema"] == list(FEATURE_SCHEMA)
        assert payload["feature_schema_version"] == FEATURE_SCHEMA_VERSION
        assert payload["model_type"] == "isolation_forest"
        assert "model" in payload and "imputer" in payload

    def test_artifacts_can_be_reliably_reloaded_after_a_fresh_process(self, db_session, tmp_path):
        # Simulates "application restart": load the artifact back from
        # disk with no reference to the in-memory objects this test
        # session already holds, and confirm it is immediately usable.
        dataset = make_dataset(benign_count=40, attack_count=0)
        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        reloaded = joblib.load(result.artifact_path)

        import numpy as np

        X = np.array([[3.0] * N_FEATURES])
        predictions = reloaded["model"].predict(reloaded["imputer"].transform(X))
        assert predictions.shape == (1,)


class TestModelRegistry:
    def test_a_successful_run_creates_exactly_one_ready_registry_row(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        row = db_session.query(ModelMetadata).filter_by(id=result.model_id).one()
        assert row.status == ModelStatus.READY
        assert row.model_type == "isolation_forest"
        assert row.artifact_path == result.artifact_path
        assert row.feature_set_version == FEATURE_SCHEMA_VERSION

    def test_does_not_appear_as_a_supervised_model(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        row = db_session.query(ModelMetadata).filter_by(id=result.model_id).one()
        assert row.model_type != "random_forest"

    def test_training_batch_ids_are_recorded_for_traceability(self, db_session, tmp_path):
        dataset = make_dataset()
        expected_batch_ids = {str(b) for b in dataset.batch_ids}

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        row = db_session.query(ModelMetadata).filter_by(id=result.model_id).one()
        assert set(row.training_batch_ids) == expected_batch_ids


class TestArtifactFailureHandling:
    def test_artifact_write_failure_leaves_no_registry_row(self, db_session, tmp_path):
        dataset = make_dataset()
        blocking_file = tmp_path / "blocked"
        blocking_file.write_text("x")

        with pytest.raises(AnomalyTrainingError):
            train_anomaly_detector(db_session, dataset, artifact_dir=blocking_file)

        assert db_session.query(ModelMetadata).count() == 0

    def test_artifact_failure_message_has_no_filesystem_path(self, db_session, tmp_path):
        dataset = make_dataset()
        blocking_file = tmp_path / "blocked"
        blocking_file.write_text("x")

        with pytest.raises(AnomalyTrainingError) as exc_info:
            train_anomaly_detector(db_session, dataset, artifact_dir=blocking_file)

        assert str(blocking_file) not in exc_info.value.message


class TestRegistryFailureHandling:
    def test_registry_write_failure_removes_the_orphaned_artifact(self, db_session, tmp_path, monkeypatch):
        dataset = make_dataset()

        def failing_commit():
            raise SQLAlchemyError("simulated failure")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(AnomalyTrainingError):
            train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert list(tmp_path.glob("*.joblib")) == []


class TestNoFakeMetrics:
    def test_validation_normal_rate_is_not_hardcoded(self, db_session, tmp_path, monkeypatch):
        dataset = make_dataset(benign_count=40, attack_count=0)
        import numpy as np
        from sklearn.ensemble import IsolationForest

        real_predict = IsolationForest.predict

        def all_anomalous(self, X):
            return np.full(len(X), -1)

        monkeypatch.setattr(IsolationForest, "predict", all_anomalous)

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert result.validation_normal_rate == 0.0
        monkeypatch.setattr(IsolationForest, "predict", real_predict)

    def test_does_not_compute_or_expose_a_risk_or_severity_field(self, db_session, tmp_path):
        dataset = make_dataset()

        result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)

        assert not hasattr(result, "risk_score")
        assert not hasattr(result, "severity")
