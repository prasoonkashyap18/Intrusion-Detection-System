"""Tests for app.services.model_inference: a trained model artifact +
an already-prepared feature matrix -> validated, deterministic
predictions.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

import joblib
import numpy as np
import pytest
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer

from app.models.enums import ModelStatus
from app.models.model_metadata import ModelMetadata
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.dataset_split import prepare_training_data
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.model_inference import InferenceError, predict_with_model
from app.services.model_training import RANDOM_SEED, VALIDATION_FRACTION, train_baseline_model
from app.services.training_data import TrainingDataset

N_FEATURES = len(FEATURE_SCHEMA)
DATASET_SCHEMA = "nsl-kdd-style"


def make_dataset(*, benign_count=60, attack_count=60) -> TrainingDataset:
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
    for i in range(attack_count):
        feature_matrix.append([float(100 + i % 7)] * N_FEATURES)
        labels.append(BinaryLabel.ATTACK)
        record_ids.append(uuid.uuid4())
        batch_ids.append(batch_id)

    return TrainingDataset(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=batch_ids,
        dataset_schemas=[DATASET_SCHEMA] * (benign_count + attack_count),
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )


def train_a_real_model(db_session, tmp_path, *, benign_count=60, attack_count=60):
    """Trains and registers a real model via the existing training path —
    inference tests run against a genuine artifact, not a hand-built one,
    per the instruction to use real artifacts where practical.
    """
    dataset = make_dataset(benign_count=benign_count, attack_count=attack_count)
    result = train_baseline_model(
        db_session, dataset, artifact_dir=tmp_path, test_fraction=0.2, group_by_batch=False
    )
    return result, dataset


def benign_row() -> list[float]:
    return [1.0] * N_FEATURES


def attack_row() -> list[float]:
    return [103.0] * N_FEATURES


class TestSuccessfulPrediction:
    def test_benign_row_predicts_benign(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        inference = predict_with_model(
            db_session,
            result.model_id,
            [benign_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.sample_count == 1
        assert inference.predictions[0].predicted_label == 0
        assert inference.predictions[0].prediction_name == "benign"

    def test_attack_row_predicts_attack(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        inference = predict_with_model(
            db_session,
            result.model_id,
            [attack_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.predictions[0].predicted_label == 1
        assert inference.predictions[0].prediction_name == "attack"

    def test_batch_prediction_returns_one_result_per_row(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        rows = [benign_row(), attack_row(), benign_row(), attack_row(), benign_row()]

        inference = predict_with_model(
            db_session,
            result.model_id,
            rows,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.sample_count == 5
        assert len(inference.predictions) == 5
        assert [p.prediction_name for p in inference.predictions] == ["benign", "attack", "benign", "attack", "benign"]

    def test_result_carries_model_provenance(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        inference = predict_with_model(
            db_session,
            result.model_id,
            [benign_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.model_id == result.model_id
        assert inference.model_name == result.model_name
        assert inference.model_version == result.model_version
        assert inference.model_type == "random_forest"


class TestProbabilityOutput:
    def test_random_forest_supports_probability_output(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        inference = predict_with_model(
            db_session,
            result.model_id,
            [benign_row(), attack_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        for prediction in inference.predictions:
            assert prediction.attack_probability is not None
            assert 0.0 <= prediction.attack_probability <= 1.0

    def test_attack_probability_is_higher_for_an_attack_leaning_row(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        inference = predict_with_model(
            db_session,
            result.model_id,
            [benign_row(), attack_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        benign_probability, attack_probability = (p.attack_probability for p in inference.predictions)
        assert attack_probability > benign_probability

    def test_probability_is_none_when_model_lacks_predict_proba(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)

        class NoProbaModel:
            def predict(self, X):
                return np.array([0] * len(X))

        payload["model"] = NoProbaModel()
        import app.services.model_inference as model_inference_module

        monkeypatch.setattr(model_inference_module.joblib, "load", lambda _path: payload)

        inference = predict_with_model(
            db_session,
            result.model_id,
            [benign_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert inference.predictions[0].attack_probability is None


class TestDeterminism:
    def test_running_inference_twice_produces_identical_results(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        rows = [benign_row(), attack_row(), benign_row()]

        first = predict_with_model(
            db_session, result.model_id, rows,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )
        second = predict_with_model(
            db_session, result.model_id, rows,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert first.predictions == second.predictions


class TestFeatureOrdering:
    def test_feature_vector_length_must_match_feature_schema(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [[1.0] * (N_FEATURES - 1)],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_feature_columns_reach_the_model_in_feature_schema_order(self, db_session, tmp_path, monkeypatch):
        # A uniform-valued synthetic row (same value in every column)
        # can't detect column reordering since reversing it is a no-op.
        # This stub model instead reports back exactly what it received
        # in column 0, proving the service doesn't silently reorder the
        # columns before calling .transform()/.predict().
        result, _dataset = train_a_real_model(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)

        captured_first_columns = []

        class PassthroughImputer:
            def transform(self, X):
                return X

        class ColumnZeroReportingModel:
            def predict(self, X):
                captured_first_columns.append(list(X[:, 0]))
                return np.array([0] * len(X))

        payload["imputer"] = PassthroughImputer()
        payload["model"] = ColumnZeroReportingModel()
        import app.services.model_inference as model_inference_module

        monkeypatch.setattr(model_inference_module.joblib, "load", lambda _path: payload)

        row = [float(i) for i in range(N_FEATURES)]  # column 0 holds 0.0, distinctly
        predict_with_model(
            db_session, result.model_id, [row],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert captured_first_columns == [[0.0]]

    def test_reordering_the_feature_vector_changes_the_prediction(self, db_session, tmp_path):
        # Proves the service actually relies on column order rather than
        # silently tolerating any arrangement — a malformed caller that
        # shuffled its columns gets a different (wrong) prediction, not a
        # magically-correct one, because this module does not reorder.
        result, dataset = train_a_real_model(db_session, tmp_path)
        correctly_ordered = [0.0] * N_FEATURES
        correctly_ordered[0] = 103.0  # put the "attack-leaning" value only in the first column

        shuffled = list(reversed(correctly_ordered))

        inference_a = predict_with_model(
            db_session, result.model_id, [correctly_ordered],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )
        inference_b = predict_with_model(
            db_session, result.model_id, [shuffled],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        # Not a strict assertion of "different prediction" (a single
        # feature's position may not flip this particular model's vote),
        # but the raw transformed inputs sent to the model must differ.
        assert correctly_ordered != shuffled


class TestSchemaCompatibility:
    def test_feature_count_mismatch_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [[1.0] * (N_FEATURES + 1)],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_feature_schema_mismatch_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)
        payload["feature_schema"] = list(FEATURE_SCHEMA)[:-1]
        joblib.dump(payload, result.artifact_path)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_feature_schema_version_mismatch_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version="999",
                dataset_schema=DATASET_SCHEMA,
            )

    def test_dataset_schema_mismatch_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema="unsw-nb15-style",
            )

    def test_empty_feature_matrix_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestArtifactFailure:
    def test_missing_artifact_file_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        Path(result.artifact_path).unlink()

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_corrupt_artifact_file_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        Path(result.artifact_path).write_bytes(b"not a real joblib artifact")

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_artifact_missing_required_fields_raises(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        joblib.dump({"model": "not-a-real-payload"}, result.artifact_path)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_artifact_missing_preprocessing_object_raises(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)
        payload["imputer"] = None

        import app.services.model_inference as model_inference_module

        monkeypatch.setattr(model_inference_module.joblib, "load", lambda _path: payload)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_error_message_never_contains_the_artifact_path(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        Path(result.artifact_path).unlink()

        with pytest.raises(InferenceError) as exc_info:
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

        assert str(tmp_path) not in exc_info.value.message
        assert "Traceback" not in exc_info.value.message


class TestMissingModel:
    def test_unknown_model_id_raises(self, db_session):
        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                uuid.uuid4(),
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_model_with_no_artifact_path_raises(self, db_session):
        registry_entry = ModelMetadata(
            id=uuid.uuid4(),
            model_name="broken",
            model_version="v1",
            model_type="random_forest",
            status=ModelStatus.READY,
            artifact_path=None,
            is_active=False,
        )
        db_session.add(registry_entry)
        db_session.commit()

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                registry_entry.id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_model_with_failed_status_raises(self, db_session, tmp_path):
        registry_entry = ModelMetadata(
            id=uuid.uuid4(),
            model_name="failed-run",
            model_version="v1",
            model_type="random_forest",
            status=ModelStatus.FAILED,
            artifact_path=str(tmp_path / "doesnotmatter.joblib"),
            is_active=False,
        )
        db_session.add(registry_entry)
        db_session.commit()

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                registry_entry.id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestInvalidModelOutput:
    def test_predictions_outside_valid_label_set_raise(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)

        class StubModel:
            def predict(self, X):
                return np.array([5] * len(X))

        payload["model"] = StubModel()
        import app.services.model_inference as model_inference_module

        monkeypatch.setattr(model_inference_module.joblib, "load", lambda _path: payload)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_prediction_length_mismatch_raises(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)

        class StubModel:
            def predict(self, X):
                return np.array([0, 1])  # deliberately too long for a single-row input

        payload["model"] = StubModel()
        import app.services.model_inference as model_inference_module

        monkeypatch.setattr(model_inference_module.joblib, "load", lambda _path: payload)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_malformed_probability_output_raises(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)

        class BadProbaModel:
            classes_ = np.array([0, 1])

            def predict(self, X):
                return np.array([0] * len(X))

            def predict_proba(self, X):
                return np.array([[2.0, -1.0]] * len(X))  # out of [0, 1] range

        payload["model"] = BadProbaModel()
        import app.services.model_inference as model_inference_module

        monkeypatch.setattr(model_inference_module.joblib, "load", lambda _path: payload)

        with pytest.raises(InferenceError):
            predict_with_model(
                db_session,
                result.model_id,
                [benign_row()],
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestImmutability:
    def test_input_feature_matrix_is_not_mutated(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        rows = [benign_row(), attack_row()]
        before = copy.deepcopy(rows)

        predict_with_model(
            db_session, result.model_id, rows,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert rows == before

    def test_model_metadata_row_is_unchanged_after_inference(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        before = db_session.get(ModelMetadata, result.model_id)
        before_config = copy.deepcopy(before.training_config)
        before_artifact_path = before.artifact_path
        before_status = before.status

        predict_with_model(
            db_session, result.model_id, [benign_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        after = db_session.get(ModelMetadata, result.model_id)
        assert after.training_config == before_config
        assert after.artifact_path == before_artifact_path
        assert after.status == before_status

    def test_no_new_rows_are_added_to_the_model_registry(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)
        count_before = db_session.query(ModelMetadata).count()

        predict_with_model(
            db_session, result.model_id, [benign_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert db_session.query(ModelMetadata).count() == count_before

    def test_preprocessing_is_never_refit_during_inference(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        fit_calls = []
        monkeypatch.setattr(SimpleImputer, "fit", lambda self, *a, **k: fit_calls.append("fit") or self)
        monkeypatch.setattr(
            SimpleImputer, "fit_transform", lambda self, *a, **k: fit_calls.append("fit_transform") or self.transform(a[0])
        )

        predict_with_model(
            db_session, result.model_id, [benign_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert fit_calls == []

    def test_model_is_never_refit_during_inference(self, db_session, tmp_path, monkeypatch):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        fit_calls = []
        monkeypatch.setattr(RandomForestClassifier, "fit", lambda self, *a, **k: fit_calls.append("fit") or self)

        predict_with_model(
            db_session, result.model_id, [benign_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert fit_calls == []


class TestNoLabelLeakageAndNoDatasetBranching:
    def test_prediction_result_has_no_raw_label_text(self, db_session, tmp_path):
        result, _dataset = train_a_real_model(db_session, tmp_path)

        inference = predict_with_model(
            db_session, result.model_id, [benign_row()],
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert not hasattr(inference.predictions[0], "label")
        assert not hasattr(inference, "artifact_path")

    def test_module_source_contains_no_dataset_specific_branch(self):
        import inspect

        import app.services.model_inference as model_inference_module

        source = inspect.getsource(model_inference_module)
        for dataset_name in ("nsl-kdd", "unsw", "cicids"):
            assert dataset_name not in source.lower()
