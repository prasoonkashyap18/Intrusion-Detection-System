"""Tests for app.services.anomaly_inference: a trained IsolationForest
artifact + an already-prepared feature matrix -> validated, deterministic
anomaly predictions — entirely independent of the supervised classifier's
test_model_inference.py counterpart.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

import joblib
import numpy as np
import pytest
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer

from app.models.enums import ModelStatus
from app.models.model_metadata import ModelMetadata
from app.services.anomaly_detection_training import train_anomaly_detector
from app.services.anomaly_inference import AnomalyInferenceError, predict_anomalies_with_model
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.training_data import TrainingDataset

N_FEATURES = len(FEATURE_SCHEMA)
DATASET_SCHEMA = "nsl-kdd-style"


def make_dataset(*, benign_count=40) -> TrainingDataset:
    feature_matrix: list[list[float | None]] = []
    labels: list[BinaryLabel] = []
    record_ids: list[uuid.UUID] = []
    batch_ids: list[uuid.UUID] = []

    batch_id = uuid.uuid4()
    for i in range(benign_count):
        feature_matrix.append([float(i % 7)] * N_FEATURES)
        labels.append(BinaryLabel.BENIGN)
        record_ids.append(uuid.uuid4())
        batch_ids.append(batch_id)

    return TrainingDataset(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=batch_ids,
        dataset_schemas=[DATASET_SCHEMA] * benign_count,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )


def train_a_real_detector(db_session, tmp_path, *, benign_count=40):
    """Trains and registers a real anomaly detector via the existing
    training path — inference tests run against a genuine artifact."""
    dataset = make_dataset(benign_count=benign_count)
    result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)
    return result, dataset


def normal_row() -> list[float]:
    return [3.0] * N_FEATURES


def far_outlier_row() -> list[float]:
    return [100000.0] * N_FEATURES


class TestSuccessfulPrediction:
    def test_normal_row_is_not_flagged_anomalous(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        inference = predict_anomalies_with_model(
            db_session,
            result.model_id,
            [normal_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.sample_count == 1
        assert inference.predictions[0].is_anomaly is False

    def test_far_outlier_row_is_flagged_anomalous(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        inference = predict_anomalies_with_model(
            db_session,
            result.model_id,
            [far_outlier_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.predictions[0].is_anomaly is True

    def test_batch_prediction_returns_one_result_per_row(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        rows = [normal_row(), far_outlier_row(), normal_row()]

        inference = predict_anomalies_with_model(
            db_session,
            result.model_id,
            rows,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.sample_count == 3
        assert len(inference.predictions) == 3

    def test_result_carries_model_provenance(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        inference = predict_anomalies_with_model(
            db_session,
            result.model_id,
            [normal_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.model_id == result.model_id
        assert inference.model_name == result.model_name
        assert inference.model_version == result.model_version
        assert inference.model_type == "isolation_forest"

    def test_anomaly_score_is_a_real_finite_number(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        inference = predict_anomalies_with_model(
            db_session,
            result.model_id,
            [normal_row(), far_outlier_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        for prediction in inference.predictions:
            assert np.isfinite(prediction.anomaly_score)

    def test_outlier_has_a_lower_anomaly_score_than_a_normal_row(self, db_session, tmp_path):
        # sklearn's own convention: lower decision_function means more
        # anomalous. A real comparison of real scores, not an assumption.
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        inference = predict_anomalies_with_model(
            db_session,
            result.model_id,
            [normal_row(), far_outlier_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        normal_score, outlier_score = (p.anomaly_score for p in inference.predictions)
        assert outlier_score < normal_score


class TestDeterminism:
    def test_running_inference_twice_produces_identical_results(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        rows = [normal_row(), far_outlier_row(), normal_row()]

        first = predict_anomalies_with_model(
            db_session, result.model_id, rows,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )
        second = predict_anomalies_with_model(
            db_session, result.model_id, rows,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert first.predictions == second.predictions


class TestInvalidFeatures:
    def test_feature_count_mismatch_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [[1.0] * (N_FEATURES + 1)],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_empty_feature_matrix_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_feature_schema_version_mismatch_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version="999",
                dataset_schema=DATASET_SCHEMA,
            )

    def test_dataset_schema_mismatch_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema="unsw-nb15-style",
            )

    def test_feature_schema_mismatch_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)
        payload["feature_schema"] = list(FEATURE_SCHEMA)[:-1]
        joblib.dump(payload, result.artifact_path)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestModelTypeIsolation:
    def test_a_supervised_random_forest_registry_row_is_rejected(self, db_session, tmp_path):
        # The registry table is shared between model families as of Step
        # 31; a row whose model_type is not "isolation_forest" must never
        # be usable through this anomaly-only path.
        registry_entry = ModelMetadata(
            id=uuid.uuid4(),
            model_name="not-an-anomaly-detector",
            model_version="v1",
            model_type="random_forest",
            dataset_name=DATASET_SCHEMA,
            feature_set_version=FEATURE_SCHEMA_VERSION,
            status=ModelStatus.READY,
            artifact_path=str(tmp_path / "doesnotmatter.joblib"),
            is_active=False,
        )
        db_session.add(registry_entry)
        db_session.commit()

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                registry_entry.id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestArtifactFailureHandling:
    def test_missing_artifact_file_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        Path(result.artifact_path).unlink()

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_corrupt_artifact_file_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        Path(result.artifact_path).write_bytes(b"not a real joblib artifact")

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_artifact_missing_required_fields_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        joblib.dump({"model": "not-a-real-payload"}, result.artifact_path)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_error_message_never_contains_the_artifact_path(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        Path(result.artifact_path).unlink()

        with pytest.raises(AnomalyInferenceError) as exc_info:
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

        assert str(tmp_path) not in exc_info.value.message
        assert "Traceback" not in exc_info.value.message


class TestMissingModel:
    def test_unknown_model_id_raises(self, db_session):
        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                uuid.uuid4(),
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_model_with_no_artifact_path_raises(self, db_session):
        registry_entry = ModelMetadata(
            id=uuid.uuid4(),
            model_name="broken",
            model_version="v1",
            model_type="isolation_forest",
            status=ModelStatus.READY,
            artifact_path=None,
            is_active=False,
        )
        db_session.add(registry_entry)
        db_session.commit()

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                registry_entry.id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_model_with_failed_status_raises(self, db_session, tmp_path):
        registry_entry = ModelMetadata(
            id=uuid.uuid4(),
            model_name="failed-run",
            model_version="v1",
            model_type="isolation_forest",
            status=ModelStatus.FAILED,
            artifact_path=str(tmp_path / "doesnotmatter.joblib"),
            is_active=False,
        )
        db_session.add(registry_entry)
        db_session.commit()

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                registry_entry.id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestInvalidModelOutput:
    def test_predictions_outside_valid_label_set_raise(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)

        class StubModel:
            def predict(self, X):
                return np.array([5] * len(X))

            def decision_function(self, X):
                return np.zeros(len(X))

        payload["model"] = StubModel()
        import app.services.anomaly_inference as anomaly_inference_module

        monkeypatch.setattr(anomaly_inference_module.joblib, "load", lambda _path: payload)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_prediction_length_mismatch_raises(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)

        class StubModel:
            def predict(self, X):
                return np.array([1, -1])  # deliberately too long for a single-row input

            def decision_function(self, X):
                return np.zeros(len(X))

        payload["model"] = StubModel()
        import app.services.anomaly_inference as anomaly_inference_module

        monkeypatch.setattr(anomaly_inference_module.joblib, "load", lambda _path: payload)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_malformed_score_output_raises(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)

        class BadScoreModel:
            def predict(self, X):
                return np.array([1] * len(X))

            def decision_function(self, X):
                return np.array([float("nan")] * len(X))

        payload["model"] = BadScoreModel()
        import app.services.anomaly_inference as anomaly_inference_module

        monkeypatch.setattr(anomaly_inference_module.joblib, "load", lambda _path: payload)

        with pytest.raises(AnomalyInferenceError):
            predict_anomalies_with_model(
                db_session,
                result.model_id,
                [normal_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestImmutability:
    def test_input_feature_matrix_is_not_mutated(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        rows = [normal_row(), far_outlier_row()]
        before = copy.deepcopy(rows)

        predict_anomalies_with_model(
            db_session, result.model_id, rows,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert rows == before

    def test_model_metadata_row_is_unchanged_after_inference(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)
        before = db_session.get(ModelMetadata, result.model_id)
        before_config = copy.deepcopy(before.training_config)
        before_artifact_path = before.artifact_path
        before_status = before.status

        predict_anomalies_with_model(
            db_session, result.model_id, [normal_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        after = db_session.get(ModelMetadata, result.model_id)
        assert after.training_config == before_config
        assert after.artifact_path == before_artifact_path
        assert after.status == before_status

    def test_preprocessing_is_never_refit_during_inference(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        fit_calls = []
        monkeypatch.setattr(SimpleImputer, "fit", lambda self, *a, **k: fit_calls.append("fit") or self)
        monkeypatch.setattr(
            SimpleImputer, "fit_transform", lambda self, *a, **k: fit_calls.append("fit_transform") or self.transform(a[0])
        )

        predict_anomalies_with_model(
            db_session, result.model_id, [normal_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert fit_calls == []

    def test_model_is_never_refit_during_inference(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        fit_calls = []
        monkeypatch.setattr(IsolationForest, "fit", lambda self, *a, **k: fit_calls.append("fit") or self)

        predict_anomalies_with_model(
            db_session, result.model_id, [normal_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert fit_calls == []


class TestNoFabricatedRiskOrSeverity:
    def test_prediction_result_has_no_risk_severity_or_confidence_field(self, db_session, tmp_path):
        result, _dataset = train_a_real_detector(db_session, tmp_path)

        inference = predict_anomalies_with_model(
            db_session, result.model_id, [normal_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        prediction = inference.predictions[0]
        assert not hasattr(prediction, "risk_score")
        assert not hasattr(prediction, "severity")
        assert not hasattr(prediction, "confidence")
        assert not hasattr(inference, "artifact_path")
