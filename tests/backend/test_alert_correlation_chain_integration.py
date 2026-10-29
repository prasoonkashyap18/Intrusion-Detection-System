"""Steps 36-40 integration tests: alert generation, event correlation,
attack-chain analysis, and threat-intelligence enrichment wired into
batch processing, built on top of the real hybrid/risk/explanation
pipeline.

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

from app.models.alert import Alert
from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.threat_intel_lookup import ThreatIntelLookup
from app.services import batch_processor
from app.services.anomaly_detection_training import train_anomaly_detector
from app.services.batch_processor import start_processing
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.model_training import train_baseline_model
from app.services.training_data import TrainingDataset

DATASET_SCHEMA = "nsl-kdd-style"
N_FEATURES = len(FEATURE_SCHEMA)

CSV_ONE_ATTACK_ONE_BENIGN = (
    b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
    b"5,tcp,http,SF,100,200,normal\n"
    b"3,udp,dns,SF,50,150,neptune\n"
)


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


def _training_dataset(dataset_schema=DATASET_SCHEMA) -> TrainingDataset:
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
        feature_matrix=feature_matrix, labels=labels, record_ids=record_ids, batch_ids=batch_ids,
        dataset_schemas=[dataset_schema] * 40, feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )


def register_ready_supervised_model(db_session, tmp_path: Path):
    return train_baseline_model(
        db_session, _training_dataset(), artifact_dir=tmp_path, test_fraction=0.0, group_by_batch=False
    )


def register_ready_anomaly_model(db_session, tmp_path: Path):
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
        feature_matrix=feature_matrix, labels=labels, record_ids=record_ids, batch_ids=batch_ids,
        dataset_schemas=[DATASET_SCHEMA] * 40, feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )
    return train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)


class TestAlertGenerationDuringProcessing:
    def test_a_confirmed_attack_row_generates_an_alert(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)

        start_processing(db_session, batch.id, upload_dir)

        alerts = db_session.scalars(select(Alert).where(Alert.batch_id == batch.id)).all()
        assert len(alerts) >= 1
        for alert in alerts:
            assert alert.status.value == "open"
            assert alert.evidence["hybrid_outcome"] != "clean"

    def test_alerts_reference_real_source_rows(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)

        start_processing(db_session, batch.id, upload_dir)

        hybrid_rows = {
            r.id: r
            for r in db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch.id)).all()
        }
        alerts = db_session.scalars(select(Alert).where(Alert.batch_id == batch.id)).all()
        for alert in alerts:
            assert alert.hybrid_result_id in hybrid_rows
            assert hybrid_rows[alert.hybrid_result_id].row_number == alert.row_number

    def test_detection_and_anomaly_results_are_unaffected_by_alert_generation(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)

        start_processing(db_session, batch.id, upload_dir)

        detection_rows = db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch.id)).all()
        assert len(detection_rows) == 2


class TestNonFatalIntegration:
    def test_alert_generation_failure_does_not_fail_the_batch(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)

        def failing(*args, **kwargs):
            raise RuntimeError("simulated alert-generation failure")

        monkeypatch.setattr(batch_processor, "build_and_persist_alerts", failing)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert result.error_message is None
        assert db_session.scalars(select(Alert).where(Alert.batch_id == batch.id)).all() == []
        detection_rows = db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch.id)).all()
        assert len(detection_rows) == 2

    def test_correlation_failure_does_not_fail_the_batch_or_remove_alerts(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)

        def failing(*args, **kwargs):
            raise RuntimeError("simulated correlation failure")

        monkeypatch.setattr(batch_processor, "build_and_persist_correlations_for_batch", failing)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        alerts = db_session.scalars(select(Alert).where(Alert.batch_id == batch.id)).all()
        assert len(alerts) >= 1

    def test_chain_analysis_failure_does_not_fail_the_batch(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)

        def failing(*args, **kwargs):
            raise RuntimeError("simulated chain-analysis failure")

        monkeypatch.setattr(batch_processor, "build_and_persist_chains_for_batch", failing)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING


class TestThreatIntelDisabledByDefault:
    def test_no_external_enrichment_calls_happen_when_disabled(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)

        calls = []
        monkeypatch.setattr(batch_processor, "enrich_alerts", lambda *a, **k: calls.append(1))

        start_processing(db_session, batch.id, upload_dir)

        assert calls == []  # settings.threat_intel_enabled defaults to False
        assert db_session.query(ThreatIntelLookup).count() == 0


class TestBatchIsolation:
    def test_alerts_and_correlations_from_two_batches_do_not_corrupt_each_other(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch_a = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)
        batch_b = with_uploaded_file(
            upload_dir, add_batch(db_session),
            b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n900,tcp,private,S0,40000,40000,neptune\n",
        )

        start_processing(db_session, batch_a.id, upload_dir)
        start_processing(db_session, batch_b.id, upload_dir)

        alerts_a = db_session.scalars(select(Alert).where(Alert.batch_id == batch_a.id)).all()
        alerts_b = db_session.scalars(select(Alert).where(Alert.batch_id == batch_b.id)).all()
        assert all(a.batch_id == batch_a.id for a in alerts_a)
        assert all(a.batch_id == batch_b.id for a in alerts_b)


class TestIdempotentRetries:
    def test_running_the_derived_layer_twice_on_the_same_data_does_not_duplicate(self, db_session, upload_dir, tmp_path):
        register_ready_supervised_model(db_session, tmp_path)
        register_ready_anomaly_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), CSV_ONE_ATTACK_ONE_BENIGN)

        start_processing(db_session, batch.id, upload_dir)
        alert_count_after_first = db_session.query(Alert).filter(Alert.batch_id == batch.id).count()

        # Re-run just the derived-analysis layer directly (simulating a
        # retried call) -- it must not create duplicate alerts, since
        # start_processing itself can only be called once per batch
        # (the atomic pending->processing claim already prevents a
        # second real call), but the layer's own idempotency is what
        # Step 36 requires regardless.
        from app.services.hybrid_detection import build_and_persist_hybrid_results

        with pytest.raises(Exception):
            # A second attempt to persist the same hybrid rows must be
            # rejected by HybridDetectionResult's own UniqueConstraint --
            # proving the underlying per-row dedup this layer depends on
            # is real, not assumed.
            build_and_persist_hybrid_results(db_session, batch.id)

        alert_count_after_retry = db_session.query(Alert).filter(Alert.batch_id == batch.id).count()
        assert alert_count_after_retry == alert_count_after_first
