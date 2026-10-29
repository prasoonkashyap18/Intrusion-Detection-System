"""Steps 33-35 integration tests: hybrid detection, risk scoring, and
explainability wired into batch processing, built on top of the real
supervised/anomaly pipelines.

Uses real fixtures throughout — real registered models trained through
the existing training paths, real CSV uploads processed through the
real `start_processing` entry point — rather than mocking the services
being integrated.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.detection_batch import DetectionBatch
from app.models.detection_explanation import DetectionExplanation
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.risk_assessment import RiskAssessment
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
    defaults = dict(id=uuid.uuid4(), filename="traffic.csv", status=ProcessingStatus.PENDING, total_records=10)
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


def register_ready_supervised_model(db_session, tmp_path: Path, *, dataset_schema=DATASET_SCHEMA):
    dataset = _supervised_training_dataset(dataset_schema)
    return train_baseline_model(
        db_session, dataset, artifact_dir=tmp_path, test_fraction=0.0, group_by_batch=False,
    )


def register_ready_anomaly_model(db_session, tmp_path: Path, *, dataset_schema=DATASET_SCHEMA):
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
    return train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)


class TestHybridRiskExplainabilityPersistence:
    def test_all_three_tables_get_one_row_per_traffic_row_with_both_models(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        hybrid_rows = db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch.id)).all()
        risk_rows = db_session.scalars(select(RiskAssessment).where(RiskAssessment.batch_id == batch.id)).all()
        explanation_rows = db_session.scalars(select(DetectionExplanation).where(DetectionExplanation.batch_id == batch.id)).all()
        assert len(hybrid_rows) == 2
        assert len(risk_rows) == 2
        assert len(explanation_rows) == 2
        assert {r.row_number for r in hybrid_rows} == {1, 2}
        assert all(r.supervised_available and r.anomaly_available for r in hybrid_rows)

    def test_hybrid_risk_rows_still_created_with_supervised_only_no_anomaly_detector(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        hybrid_rows = db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch.id)).all()
        assert len(hybrid_rows) == 2
        assert all(r.supervised_available and not r.anomaly_available for r in hybrid_rows)
        risk_rows = db_session.scalars(select(RiskAssessment).where(RiskAssessment.batch_id == batch.id)).all()
        assert len(risk_rows) == 2
        assert all(r.anomaly_component is None for r in risk_rows)

    def test_risk_score_and_severity_are_bounded_and_consistent_with_hybrid_outcome(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        risk_rows = {
            r.row_number: r
            for r in db_session.scalars(select(RiskAssessment).where(RiskAssessment.batch_id == batch.id)).all()
        }
        for row in risk_rows.values():
            assert 0.0 <= row.risk_score <= 100.0

    def test_explanations_reference_the_correct_hybrid_and_risk_rows(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        explanations = db_session.scalars(select(DetectionExplanation).where(DetectionExplanation.batch_id == batch.id)).all()
        hybrid_by_id = {
            r.id: r
            for r in db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch.id)).all()
        }
        risk_by_id = {
            r.id: r
            for r in db_session.scalars(select(RiskAssessment).where(RiskAssessment.batch_id == batch.id)).all()
        }
        for explanation in explanations:
            hybrid_row = hybrid_by_id[explanation.hybrid_result_id]
            risk_row = risk_by_id[explanation.risk_assessment_id]
            assert hybrid_row.row_number == explanation.row_number == risk_row.row_number
            assert explanation.explanation["measured_values"]["hybrid_outcome"] == hybrid_row.outcome
            assert explanation.explanation["measured_values"]["risk_score"] == pytest.approx(risk_row.risk_score)


class TestNonFatalIntegration:
    def test_a_hybrid_persistence_failure_does_not_fail_the_batch_or_earlier_persisted_results(
        self, db_session, upload_dir, tmp_path, monkeypatch
    ):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        def failing_build(*args, **kwargs):
            raise RuntimeError("simulated hybrid-detection failure")

        monkeypatch.setattr(batch_processor, "build_and_persist_hybrid_results", failing_build)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert result.error_message is None
        detection_rows = db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch.id)).all()
        assert len(detection_rows) == 2
        hybrid_rows = db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch.id)).all()
        assert hybrid_rows == []

    def test_no_supervised_results_means_the_layer_is_skipped_not_failed(self, db_session, upload_dir, tmp_path):
        # No models registered at all -- supervised inference itself
        # fails the batch (pre-existing Step 26 behavior), and this
        # derived layer must not run (nothing to combine) or raise.
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED
        hybrid_rows = db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch.id)).all()
        assert hybrid_rows == []


class TestBatchIsolation:
    def test_hybrid_risk_explanation_rows_from_two_batches_never_mix(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch_a = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)
        batch_b = with_uploaded_file(
            upload_dir,
            add_batch(db_session),
            b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n7,tcp,http,SF,10,20,normal\n",
        )

        start_processing(db_session, batch_a.id, upload_dir)
        start_processing(db_session, batch_b.id, upload_dir)

        rows_a = db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch_a.id)).all()
        rows_b = db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch_b.id)).all()
        assert len(rows_a) == 2
        assert len(rows_b) == 1
        assert all(r.batch_id == batch_a.id for r in rows_a)
        assert all(r.batch_id == batch_b.id for r in rows_b)
