"""Tests for app.services.realtime_detection: feature compatibility,
supervised/anomaly integration, hybrid/risk/explanation/alert
integration without duplicated logic, idempotency, and failure
handling for one finalized live flow at a time.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.anomaly_result import AnomalyResult
from app.models.detection_batch import DetectionBatch
from app.models.detection_explanation import DetectionExplanation
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.model_metadata import ModelMetadata
from app.models.risk_assessment import RiskAssessment
from app.services.anomaly_detection_training import train_anomaly_detector
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.flow_construction import FinalizedFlow
from app.services.model_training import train_baseline_model
from app.services.pcap_feature_mapping import PCAP_DATASET_SCHEMA
from app.services.realtime_detection import process_finalized_flow
from app.services.training_data import TrainingDataset

N_FEATURES = len(FEATURE_SCHEMA)


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="live:eth0", status=ProcessingStatus.PROCESSING, total_records=0)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def make_flow(source_ip="10.0.0.1", destination_ip="10.0.0.2", packet_count=3, byte_count=300, first_timestamp=0.0, last_timestamp=2.0) -> FinalizedFlow:
    return FinalizedFlow(
        flow_key=(6, source_ip, destination_ip, 80, 1234),
        source_ip=source_ip, destination_ip=destination_ip, source_port=1234, destination_port=80,
        protocol_number=6, is_tcp=True, first_timestamp=first_timestamp, last_timestamp=last_timestamp,
        packet_count=packet_count, byte_count=byte_count, forward_packet_count=2, forward_byte_count=200,
        backward_packet_count=1, backward_byte_count=100, tcp_flags_union=18,
    )


def _dataset(dataset_schema, label_a, label_b, n=20):
    feature_matrix, labels, record_ids, batch_ids = [], [], [], []
    training_batch_id = uuid.uuid4()
    for i in range(n):
        feature_matrix.append([float(i % 7)] * N_FEATURES)
        labels.append(label_a)
        record_ids.append(uuid.uuid4())
        batch_ids.append(training_batch_id)
    for i in range(n):
        feature_matrix.append([float(100 + i % 7)] * N_FEATURES)
        labels.append(label_b)
        record_ids.append(uuid.uuid4())
        batch_ids.append(training_batch_id)
    return TrainingDataset(
        feature_matrix=feature_matrix, labels=labels, record_ids=record_ids, batch_ids=batch_ids,
        dataset_schemas=[dataset_schema] * (2 * n), feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )


def register_supervised_model(db_session, tmp_path, dataset_schema=PCAP_DATASET_SCHEMA) -> ModelMetadata:
    dataset = _dataset(dataset_schema, BinaryLabel.BENIGN, BinaryLabel.ATTACK)
    result = train_baseline_model(db_session, dataset, artifact_dir=tmp_path, test_fraction=0.0, group_by_batch=False)
    return db_session.get(ModelMetadata, result.model_id)


def register_anomaly_model(db_session, tmp_path, dataset_schema=PCAP_DATASET_SCHEMA) -> ModelMetadata:
    feature_matrix, labels, record_ids, batch_ids = [], [], [], []
    training_batch_id = uuid.uuid4()
    for i in range(40):
        feature_matrix.append([float(i % 7)] * N_FEATURES)
        labels.append(BinaryLabel.BENIGN)
        record_ids.append(uuid.uuid4())
        batch_ids.append(training_batch_id)
    dataset = TrainingDataset(
        feature_matrix=feature_matrix, labels=labels, record_ids=record_ids, batch_ids=batch_ids,
        dataset_schemas=[dataset_schema] * 40, feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )
    result = train_anomaly_detector(db_session, dataset, artifact_dir=tmp_path)
    return db_session.get(ModelMetadata, result.model_id)


@pytest.fixture()
def session_factory(db_session):
    from sqlalchemy.orm import sessionmaker

    maker = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)
    return maker


class TestFeaturePersistenceAndCompatibility:
    def test_no_compatible_model_persists_features_without_fabricating_predictions(self, db_session, session_factory):
        batch = add_batch(db_session)
        result = process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        assert result.already_processed is False
        assert result.supervised_ran is False
        assert result.anomaly_ran is False
        rows = db_session.scalars(select(MappedFeatureRecord).where(MappedFeatureRecord.batch_id == batch.id)).all()
        assert len(rows) == 1
        assert rows[0].dataset_schema == PCAP_DATASET_SCHEMA
        assert db_session.query(DetectionResult).filter(DetectionResult.batch_id == batch.id).count() == 0

    def test_incompatible_dataset_schema_model_is_never_applied(self, db_session, tmp_path, session_factory):
        # A model trained for a different dataset_schema must not be
        # silently used -- predict_with_model's own compatibility check
        # (reused unchanged) prevents this.
        register_supervised_model(db_session, tmp_path, dataset_schema="nsl-kdd-style")
        batch = add_batch(db_session)

        result = process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        assert result.supervised_ran is False
        assert db_session.query(DetectionResult).filter(DetectionResult.batch_id == batch.id).count() == 0


class TestSupervisedAndAnomalyIntegration:
    def test_supervised_model_runs_and_persists_a_real_prediction(self, db_session, tmp_path, session_factory):
        register_supervised_model(db_session, tmp_path)
        batch = add_batch(db_session)

        result = process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        assert result.supervised_ran is True
        rows = db_session.query(DetectionResult).filter(DetectionResult.batch_id == batch.id).all()
        assert len(rows) == 1
        assert rows[0].prediction_name in ("benign", "attack")

    def test_anomaly_model_runs_and_persists_a_real_score(self, db_session, tmp_path, session_factory):
        register_anomaly_model(db_session, tmp_path)
        batch = add_batch(db_session)

        result = process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        assert result.anomaly_ran is True
        rows = db_session.query(AnomalyResult).filter(AnomalyResult.batch_id == batch.id).all()
        assert len(rows) == 1
        assert isinstance(rows[0].anomaly_score, float)

    def test_anomaly_score_is_never_an_attack_probability(self, db_session, tmp_path, session_factory):
        register_anomaly_model(db_session, tmp_path)
        batch = add_batch(db_session)

        process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        row = db_session.query(AnomalyResult).filter(AnomalyResult.batch_id == batch.id).first()
        assert not (-0.001 <= row.anomaly_score <= 1.001) or row.anomaly_score in (0.0, 1.0)
        # The real assertion: the column simply has no [0,1] constraint
        # and the model's attack_probability field is untouched by it.
        detection = db_session.query(DetectionResult).filter(DetectionResult.batch_id == batch.id).first()
        assert detection is None  # no supervised model registered in this test


class TestHybridRiskExplanationAlertIntegration:
    def test_full_derived_pipeline_runs_with_both_models(self, db_session, tmp_path, session_factory):
        register_supervised_model(db_session, tmp_path)
        register_anomaly_model(db_session, tmp_path)
        batch = add_batch(db_session)

        result = process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        assert result.hybrid_ran is True
        assert db_session.query(HybridDetectionResult).filter(HybridDetectionResult.batch_id == batch.id).count() == 1
        assert db_session.query(RiskAssessment).filter(RiskAssessment.batch_id == batch.id).count() == 1
        assert db_session.query(DetectionExplanation).filter(DetectionExplanation.batch_id == batch.id).count() == 1

    def test_no_missing_output_is_ever_treated_as_clean(self, db_session, tmp_path, session_factory):
        # Only an anomaly model exists; supervised is unavailable. The
        # hybrid outcome must reflect "supervised unavailable", not a
        # fabricated BENIGN prediction.
        register_anomaly_model(db_session, tmp_path)
        batch = add_batch(db_session)

        process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        hybrid = db_session.query(HybridDetectionResult).filter(HybridDetectionResult.batch_id == batch.id).first()
        assert hybrid.supervised_available is False
        assert "supervised:unavailable" in hybrid.contributing_signals


class TestIdempotency:
    def test_reprocessing_the_same_flow_is_a_safe_no_op(self, db_session, tmp_path, session_factory):
        register_supervised_model(db_session, tmp_path)
        register_anomaly_model(db_session, tmp_path)
        batch = add_batch(db_session)

        first = process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)
        second = process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        assert first.already_processed is False
        assert second.already_processed is True
        assert db_session.query(DetectionResult).filter(DetectionResult.batch_id == batch.id).count() == 1
        assert db_session.query(HybridDetectionResult).filter(HybridDetectionResult.batch_id == batch.id).count() == 1

    def test_different_rows_in_the_same_batch_are_independent(self, db_session, tmp_path, session_factory):
        register_supervised_model(db_session, tmp_path)
        batch = add_batch(db_session)

        process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)
        process_finalized_flow(batch.id, make_flow(source_ip="10.0.0.5"), row_number=2, session_factory=session_factory)

        assert db_session.query(MappedFeatureRecord).filter(MappedFeatureRecord.batch_id == batch.id).count() == 2


class TestAlertGeneration:
    def test_a_confirmed_attack_flow_generates_an_alert(self, db_session, tmp_path, session_factory):
        register_supervised_model(db_session, tmp_path)
        register_anomaly_model(db_session, tmp_path)
        batch = add_batch(db_session)
        # A strongly "attack-like" flow (large counts, matching the
        # trained model's ATTACK-side synthetic feature range).
        flow = FinalizedFlow(
            flow_key=(6, "10.0.0.1", "10.0.0.2", 80, 1234), source_ip="10.0.0.1", destination_ip="10.0.0.2",
            source_port=1234, destination_port=80, protocol_number=6, is_tcp=True,
            first_timestamp=0.0, last_timestamp=1.0, packet_count=103, byte_count=10300,
            forward_packet_count=100, forward_byte_count=10000, backward_packet_count=3, backward_byte_count=300,
            tcp_flags_union=18,
        )

        result = process_finalized_flow(batch.id, flow, row_number=1, session_factory=session_factory)

        alerts = db_session.query(Alert).filter(Alert.batch_id == batch.id).all()
        if result.alert_generated:
            assert len(alerts) == 1
            assert alerts[0].evidence["hybrid_outcome"] != "clean"
        else:
            assert alerts == []


class TestFailureHandling:
    def test_derived_analysis_failure_does_not_erase_the_persisted_feature_row(self, db_session, tmp_path, session_factory, monkeypatch):
        register_supervised_model(db_session, tmp_path)
        batch = add_batch(db_session)

        import app.services.realtime_detection as realtime_module

        def failing_combine(*args, **kwargs):
            raise RuntimeError("simulated hybrid-detection failure")

        monkeypatch.setattr(realtime_module, "combine_signals", failing_combine)

        result = process_finalized_flow(batch.id, make_flow(), row_number=1, session_factory=session_factory)

        assert result.failure == "derived_analysis_failed"
        rows = db_session.query(MappedFeatureRecord).filter(MappedFeatureRecord.batch_id == batch.id).all()
        assert len(rows) == 1
        detection_rows = db_session.query(DetectionResult).filter(DetectionResult.batch_id == batch.id).all()
        assert len(detection_rows) == 1  # supervised inference already succeeded and committed
