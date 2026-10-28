"""Step 31 integration tests: anomaly detection wired into batch
processing, independently of the supervised classifier.

Uses real fixtures throughout — real registered models trained through
the existing training paths, real CSV uploads processed through the real
`start_processing` entry point — rather than mocking the services being
integrated.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ModelStatus, ProcessingStatus
from app.models.model_metadata import ModelMetadata
from app.services import batch_processor
from app.services.anomaly_detection_training import train_anomaly_detector
from app.services.batch_processor import start_processing
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.model_training import train_baseline_model
from app.services.training_data import TrainingDataset

NSL_KDD_CSV = (
    b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
    b"5,tcp,http,SF,100,200,normal\n"
    b"3,udp,dns,SF,50,150,neptune\n"
)

DATASET_SCHEMA = "nsl-kdd-style"
N_FEATURES = len(FEATURE_SCHEMA)


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "uploads"
    directory.mkdir()
    return directory


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(
        id=uuid.uuid4(), filename="traffic.csv", status=ProcessingStatus.PENDING, total_records=10
    )
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def with_uploaded_file(upload_dir: Path, batch: DetectionBatch, content: bytes) -> DetectionBatch:
    (upload_dir / f"{batch.id}.csv").write_bytes(content)
    return batch


def _supervised_training_dataset(dataset_schema=DATASET_SCHEMA) -> TrainingDataset:
    feature_matrix: list[list[float | None]] = []
    labels: list[BinaryLabel] = []
    record_ids: list[uuid.UUID] = []
    batch_ids: list[uuid.UUID] = []
    training_batch_id = uuid.uuid4()
    for i in range(20):
        feature_matrix.append([float(i % 7)] * N_FEATURES)
        labels.append(BinaryLabel.BENIGN)
        record_ids.append(uuid.uuid4())
        batch_ids.append(training_batch_id)
    for i in range(20):
        feature_matrix.append([float(100 + i % 7)] * N_FEATURES)
        labels.append(BinaryLabel.ATTACK)
        record_ids.append(uuid.uuid4())
        batch_ids.append(training_batch_id)
    return TrainingDataset(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=batch_ids,
        dataset_schemas=[dataset_schema] * 40,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )


def register_ready_supervised_model(db_session, tmp_path: Path, *, dataset_schema=DATASET_SCHEMA, model_version=None):
    dataset = _supervised_training_dataset(dataset_schema)
    return train_baseline_model(
        db_session,
        dataset,
        artifact_dir=tmp_path,
        test_fraction=0.0,
        group_by_batch=False,
        model_version=model_version,
    )


def register_ready_anomaly_model(db_session, tmp_path: Path, *, dataset_schema=DATASET_SCHEMA, model_version=None):
    feature_matrix: list[list[float | None]] = []
    labels: list[BinaryLabel] = []
    record_ids: list[uuid.UUID] = []
    batch_ids: list[uuid.UUID] = []
    training_batch_id = uuid.uuid4()
    for i in range(40):
        feature_matrix.append([float(i % 7)] * N_FEATURES)
        labels.append(BinaryLabel.BENIGN)
        record_ids.append(uuid.uuid4())
        batch_ids.append(training_batch_id)
    dataset = TrainingDataset(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=batch_ids,
        dataset_schemas=[dataset_schema] * 40,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )
    return train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path, model_version=model_version)


class TestAnomalyDetectionRunsIndependently:
    def test_anomaly_detection_runs_alongside_supervised_inference(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        calls = []
        real_predict = batch_processor.predict_anomalies_with_model

        def spy_predict(*args, **kwargs):
            calls.append((args, kwargs))
            return real_predict(*args, **kwargs)

        monkeypatch.setattr(batch_processor, "predict_anomalies_with_model", spy_predict)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert len(calls) == 1

    def test_batch_still_succeeds_when_no_anomaly_detector_exists(self, db_session, upload_dir, tmp_path):
        # Only a supervised model is registered — no anomaly detector.
        # The batch must succeed exactly as it did before Step 31.
        register_ready_supervised_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert result.error_message is None

    def test_batch_still_succeeds_when_anomaly_detection_itself_fails(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        def failing_predict(*args, **kwargs):
            raise RuntimeError("simulated unexpected anomaly-detection failure")

        monkeypatch.setattr(batch_processor, "predict_anomalies_with_model", failing_predict)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert result.error_message is None

    def test_no_anomaly_detector_does_not_prevent_supervised_results_from_persisting(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        rows = db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch.id)).all()
        assert len(rows) == 2


class TestAnomalyDetectionDoesNotPersistOrLeak:
    def test_no_detection_result_rows_are_created_from_anomaly_predictions(self, db_session, upload_dir, tmp_path):
        # Register ONLY an anomaly detector (no supervised model), so any
        # DetectionResult rows that appear could only have come from
        # anomaly detection — there must be none, since supervised
        # inference will fail (no compatible model) and anomaly detection
        # never persists anything regardless.
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        rows = db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch.id)).all()
        assert rows == []

    def test_batch_fails_when_only_an_anomaly_detector_exists_and_no_supervised_model(self, db_session, upload_dir, tmp_path):
        # Supervised inference is still strict (Step 26 behavior
        # unchanged): no compatible supervised model means the batch
        # fails, regardless of whether an anomaly detector is available.
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED


class TestModelTypeSelectionIsolation:
    def test_select_ready_model_never_returns_an_anomaly_detector(self, db_session, tmp_path):
        register_ready_anomaly_model(db_session, tmp_path)

        from app.services.batch_processor import _select_ready_model

        selected = _select_ready_model(db_session, DATASET_SCHEMA, FEATURE_SCHEMA_VERSION)
        assert selected is None

    def test_select_ready_anomaly_model_never_returns_a_supervised_model(self, db_session, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)

        from app.services.batch_processor import _select_ready_anomaly_model

        selected = _select_ready_anomaly_model(db_session, DATASET_SCHEMA, FEATURE_SCHEMA_VERSION)
        assert selected is None

    def test_each_selector_finds_its_own_model_type_when_both_exist(self, db_session, tmp_path):
        supervised = register_ready_supervised_model(db_session, tmp_path)
        anomaly = register_ready_anomaly_model(db_session, tmp_path)

        from app.services.batch_processor import _select_ready_anomaly_model, _select_ready_model

        assert _select_ready_model(db_session, DATASET_SCHEMA, FEATURE_SCHEMA_VERSION).id == supervised.model_id
        assert _select_ready_anomaly_model(db_session, DATASET_SCHEMA, FEATURE_SCHEMA_VERSION).id == anomaly.model_id


class TestBatchIsolation:
    def test_anomaly_detection_uses_only_this_batchs_own_features(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch_a = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)
        batch_b = with_uploaded_file(
            upload_dir,
            add_batch(db_session),
            b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n7,tcp,http,SF,10,20,normal\n",
        )

        captured_sizes = []
        real_predict = batch_processor.predict_anomalies_with_model

        def spy_predict(db, model_id, feature_matrix, **kwargs):
            captured_sizes.append(len(feature_matrix))
            return real_predict(db, model_id, feature_matrix, **kwargs)

        monkeypatch.setattr(batch_processor, "predict_anomalies_with_model", spy_predict)

        start_processing(db_session, batch_a.id, upload_dir)
        start_processing(db_session, batch_b.id, upload_dir)

        assert captured_sizes == [2, 1]


class TestExistingBehaviorUnaffected:
    def test_supervised_classification_results_are_unchanged_by_anomaly_detection(self, db_session, upload_dir, tmp_path):
        supervised = register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        rows = db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch.id)).all()
        assert len(rows) == 2
        assert all(r.model_id == supervised.model_id for r in rows)
        assert {r.prediction_name for r in rows} <= {"benign", "attack"}
