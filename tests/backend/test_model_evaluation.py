"""Tests for app.services.model_evaluation: a trained model artifact +
the untouched test partition -> a truthful EvaluationResult.
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
from app.services.dataset_split import DatasetPartition, prepare_training_data
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.model_evaluation import EvaluationError, evaluate_model
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


def train_and_prepare(db_session, tmp_path, *, benign_count=60, attack_count=60):
    """Trains and registers a real model, and independently reproduces the
    same test partition it was trained alongside (deterministic given the
    same dataset/config/seed) — so tests evaluate against the exact held-
    out data that model's own training run set aside.
    """
    dataset = make_dataset(benign_count=benign_count, attack_count=attack_count)
    result = train_baseline_model(
        db_session, dataset, artifact_dir=tmp_path, test_fraction=0.2, group_by_batch=False
    )
    prepared = prepare_training_data(
        dataset,
        test_fraction=0.2,
        validation_fraction=VALIDATION_FRACTION,
        random_seed=RANDOM_SEED,
        group_by_batch=False,
    )
    return result, prepared


def empty_partition() -> DatasetPartition:
    return DatasetPartition(
        feature_matrix=[],
        labels=[],
        record_ids=[],
        batch_ids=[],
        sample_count=0,
        class_distribution={"benign": 0, "attack": 0},
        class_proportions={"benign": 0.0, "attack": 0.0},
    )


class TestSuccessfulEvaluation:
    def test_evaluate_returns_real_metrics_from_predictions(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        evaluation = evaluate_model(
            db_session,
            result.model_id,
            prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert evaluation.test_sample_count == prepared.test.sample_count
        assert set(evaluation.metrics) == {"accuracy", "precision", "recall", "f1"}
        for value in evaluation.metrics.values():
            assert 0.0 <= value <= 1.0

    def test_confusion_matrix_sums_to_test_sample_count(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        evaluation = evaluate_model(
            db_session,
            result.model_id,
            prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        cm = evaluation.confusion_matrix
        assert cm["tp"] + cm["tn"] + cm["fp"] + cm["fn"] == evaluation.test_sample_count

    def test_accuracy_matches_an_independent_manual_computation(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        evaluation = evaluate_model(
            db_session,
            result.model_id,
            prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        payload = joblib.load(result.artifact_path)
        X_test = np.array(prepared.test.feature_matrix, dtype=float)
        y_test = np.array([int(label) for label in prepared.test.labels], dtype=int)
        manual_predictions = payload["model"].predict(payload["imputer"].transform(X_test))
        manual_accuracy = float(np.mean(manual_predictions == y_test))

        assert evaluation.metrics["accuracy"] == pytest.approx(manual_accuracy)

    def test_precision_and_recall_are_consistent_with_the_confusion_matrix(self, db_session, tmp_path, monkeypatch):
        # precision/recall are not symmetric in (predictions, labels) the
        # way accuracy is. A perfectly-separable synthetic dataset makes
        # every metric 1.0 regardless of argument order, which would hide
        # an accidental swap — so this test forces deliberately imperfect,
        # known predictions and checks the metrics by hand from the
        # resulting confusion counts.
        result, prepared = train_and_prepare(db_session, tmp_path, benign_count=90, attack_count=30)

        y_test = [int(label) for label in prepared.test.labels]
        # Predict everything as BENIGN except flip the first ATTACK-labeled
        # row to BENIGN too (a false negative) and the first BENIGN-labeled
        # row to ATTACK (a false positive) — guarantees tp < total attacks
        # and fp > 0, so precision and recall differ unless swapped.
        predictions = [0] * len(y_test)
        first_attack_pos = y_test.index(1)
        first_benign_pos = y_test.index(0)
        predictions[first_attack_pos] = 0  # false negative (already 0, stays wrong)
        predictions[first_benign_pos] = 1  # false positive
        # Give a couple of true positives so precision has a non-zero numerator.
        attack_positions = [i for i, label in enumerate(y_test) if label == 1]
        for pos in attack_positions[1:4]:
            predictions[pos] = 1

        class StubModel:
            def predict(self, X):
                return np.array(predictions)

        payload = joblib.load(result.artifact_path)
        payload["model"] = StubModel()
        import app.services.model_evaluation as model_evaluation_module

        monkeypatch.setattr(model_evaluation_module.joblib, "load", lambda _path: payload)

        evaluation = evaluate_model(
            db_session,
            result.model_id,
            prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        tp = sum(1 for p, y in zip(predictions, y_test) if p == 1 and y == 1)
        fp = sum(1 for p, y in zip(predictions, y_test) if p == 1 and y == 0)
        fn = sum(1 for p, y in zip(predictions, y_test) if p == 0 and y == 1)
        tn = sum(1 for p, y in zip(predictions, y_test) if p == 0 and y == 0)
        expected_precision = tp / (tp + fp) if (tp + fp) else 0.0
        expected_recall = tp / (tp + fn) if (tp + fn) else 0.0
        assert expected_precision != expected_recall  # otherwise this test can't distinguish a swap
        assert tp != tn  # otherwise this test can't distinguish a tp/tn mix-up

        assert evaluation.metrics["precision"] == pytest.approx(expected_precision)
        assert evaluation.metrics["recall"] == pytest.approx(expected_recall)
        assert evaluation.confusion_matrix == {"tp": tp, "fp": fp, "fn": fn, "tn": tn}

    def test_class_counts_reflect_real_test_partition_labels(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path, benign_count=80, attack_count=40)

        evaluation = evaluate_model(
            db_session,
            result.model_id,
            prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert evaluation.class_counts == prepared.test.class_distribution


class TestClassLevelResults:
    def test_class_metrics_cover_both_benign_and_attack(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        evaluation = evaluate_model(
            db_session,
            result.model_id,
            prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert set(evaluation.class_metrics) == {"benign", "attack"}
        for class_name in ("benign", "attack"):
            assert set(evaluation.class_metrics[class_name]) == {"precision", "recall", "f1", "support"}

    def test_class_support_sums_match_class_counts(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        evaluation = evaluate_model(
            db_session,
            result.model_id,
            prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_schema=DATASET_SCHEMA,
        )

        assert evaluation.class_metrics["benign"]["support"] == evaluation.class_counts["benign"]
        assert evaluation.class_metrics["attack"]["support"] == evaluation.class_counts["attack"]


class TestEmptyAndInsufficientTestSets:
    def test_empty_test_set_raises(self, db_session, tmp_path):
        result, _prepared = train_and_prepare(db_session, tmp_path)

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                empty_partition(),
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_single_class_test_set_raises(self, db_session, tmp_path):
        result, _prepared = train_and_prepare(db_session, tmp_path)
        single_class = DatasetPartition(
            feature_matrix=[[1.0] * N_FEATURES] * 5,
            labels=[BinaryLabel.BENIGN] * 5,
            record_ids=[uuid.uuid4() for _ in range(5)],
            batch_ids=[uuid.uuid4() for _ in range(5)],
            sample_count=5,
            class_distribution={"benign": 5, "attack": 0},
            class_proportions={"benign": 1.0, "attack": 0.0},
        )

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                single_class,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestSchemaCompatibility:
    def test_feature_schema_mismatch_raises(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)
        payload["feature_schema"] = list(FEATURE_SCHEMA)[:-1]  # drop one column
        joblib.dump(payload, result.artifact_path)

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_feature_schema_version_mismatch_raises(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version="999",
                dataset_schema=DATASET_SCHEMA,
            )

    def test_dataset_schema_mismatch_raises(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema="unsw-nb15-style",
            )

    def test_feature_count_mismatch_raises(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        wrong_width = DatasetPartition(
            feature_matrix=[row[:-1] for row in prepared.test.feature_matrix],
            labels=prepared.test.labels,
            record_ids=prepared.test.record_ids,
            batch_ids=prepared.test.batch_ids,
            sample_count=prepared.test.sample_count,
            class_distribution=prepared.test.class_distribution,
            class_proportions=prepared.test.class_proportions,
        )

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                wrong_width,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestModelArtifactFailure:
    def test_missing_artifact_file_raises(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        Path(result.artifact_path).unlink()

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_corrupt_artifact_file_raises(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        Path(result.artifact_path).write_bytes(b"not a real joblib artifact")

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_artifact_missing_required_fields_raises(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        joblib.dump({"model": "not-a-real-payload"}, result.artifact_path)

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_error_message_never_contains_the_artifact_path(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        Path(result.artifact_path).unlink()

        with pytest.raises(EvaluationError) as exc_info:
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

        assert str(tmp_path) not in exc_info.value.message
        assert "Traceback" not in exc_info.value.message


class TestMissingModelMetadata:
    def test_unknown_model_id_raises(self, db_session):
        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                uuid.uuid4(),
                empty_partition(),
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

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                registry_entry.id,
                empty_partition(),
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

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                registry_entry.id,
                empty_partition(),
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class _PredictsOutOfRangeValues:
    """Module-level (picklable only in spirit — never actually written to
    disk) stand-in used by swapping `joblib.load`'s return value directly,
    so the real artifact file never needs to hold an unpicklable object."""

    def predict(self, X):
        return np.array([5] * len(X))


class _PredictsTooFewValues:
    def predict(self, X):
        return np.array([0, 1])  # deliberately too short


class TestInvalidModelOutput:
    def test_predictions_outside_valid_label_set_raise(self, db_session, tmp_path, monkeypatch):
        result, prepared = train_and_prepare(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)
        payload["model"] = _PredictsOutOfRangeValues()

        import app.services.model_evaluation as model_evaluation_module

        monkeypatch.setattr(model_evaluation_module.joblib, "load", lambda _path: payload)

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )

    def test_prediction_label_length_mismatch_raises(self, db_session, tmp_path, monkeypatch):
        result, prepared = train_and_prepare(db_session, tmp_path)
        payload = joblib.load(result.artifact_path)
        payload["model"] = _PredictsTooFewValues()

        import app.services.model_evaluation as model_evaluation_module

        monkeypatch.setattr(model_evaluation_module.joblib, "load", lambda _path: payload)

        with pytest.raises(EvaluationError):
            evaluate_model(
                db_session,
                result.model_id,
                prepared.test,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                dataset_schema=DATASET_SCHEMA,
            )


class TestDeterministicEvaluation:
    def test_evaluating_twice_produces_identical_metrics(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        first = evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )
        second = evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert first.metrics == second.metrics
        assert first.confusion_matrix == second.confusion_matrix
        assert first.class_metrics == second.class_metrics


class TestTestSetAndModelImmutability:
    def test_test_partition_is_not_mutated(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        before_features = copy.deepcopy(prepared.test.feature_matrix)
        before_labels = copy.deepcopy(prepared.test.labels)

        evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert prepared.test.feature_matrix == before_features
        assert prepared.test.labels == before_labels

    def test_preprocessing_is_never_refit_during_evaluation(self, db_session, tmp_path, monkeypatch):
        result, prepared = train_and_prepare(db_session, tmp_path)

        fit_calls = []
        monkeypatch.setattr(SimpleImputer, "fit", lambda self, *a, **k: fit_calls.append("fit") or self)
        monkeypatch.setattr(
            SimpleImputer, "fit_transform", lambda self, *a, **k: fit_calls.append("fit_transform") or self.transform(a[0])
        )

        evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert fit_calls == []

    def test_model_is_never_refit_during_evaluation(self, db_session, tmp_path, monkeypatch):
        result, prepared = train_and_prepare(db_session, tmp_path)

        fit_calls = []
        monkeypatch.setattr(RandomForestClassifier, "fit", lambda self, *a, **k: fit_calls.append("fit") or self)

        evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert fit_calls == []

    def test_model_metadata_row_is_unchanged_after_evaluation(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        before = db_session.get(ModelMetadata, result.model_id)
        before_config = copy.deepcopy(before.training_config)
        before_artifact_path = before.artifact_path
        before_status = before.status

        evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        after = db_session.get(ModelMetadata, result.model_id)
        assert after.training_config == before_config
        assert after.artifact_path == before_artifact_path
        assert after.status == before_status

    def test_no_new_rows_are_added_to_the_model_registry(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)
        count_before = db_session.query(ModelMetadata).count()

        evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert db_session.query(ModelMetadata).count() == count_before


class TestNoMetadataLeakage:
    def test_result_has_no_filesystem_path_field(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        evaluation = evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert not hasattr(evaluation, "artifact_path")

    def test_result_has_no_raw_feature_vectors(self, db_session, tmp_path):
        result, prepared = train_and_prepare(db_session, tmp_path)

        evaluation = evaluate_model(
            db_session, result.model_id, prepared.test,
            feature_schema_version=FEATURE_SCHEMA_VERSION, dataset_schema=DATASET_SCHEMA,
        )

        assert not hasattr(evaluation, "feature_matrix")
        assert not hasattr(evaluation, "predictions")
