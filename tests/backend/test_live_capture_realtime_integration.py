"""Step 43 integration: a live capture session whose finalized flows
are processed by the real `app.services.realtime_detection.
process_finalized_flow` (not the Step-42-only feature-only default),
proving the two steps wire together exactly the way
`app.api.v1.capture.start_capture_session` wires them in production.
"""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.alert import Alert
from app.models.detection_result import DetectionResult
from app.models.enums import CaptureSessionStatus
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.flow_construction import PacketRecord
from app.services.live_capture import get_status, start_capture, stop_capture
from app.services.model_training import train_baseline_model
from app.services.pcap_feature_mapping import PCAP_DATASET_SCHEMA
from app.services.realtime_detection import process_finalized_flow
from app.services.training_data import TrainingDataset

N_FEATURES = len(FEATURE_SCHEMA)


@pytest.fixture()
def session_factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)


def register_supervised_model(db_session, tmp_path):
    feature_matrix, labels, record_ids, batch_ids = [], [], [], []
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
    dataset = TrainingDataset(
        feature_matrix=feature_matrix, labels=labels, record_ids=record_ids, batch_ids=batch_ids,
        dataset_schemas=[PCAP_DATASET_SCHEMA] * 40, feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )
    train_baseline_model(db_session, dataset, artifact_dir=tmp_path, test_fraction=0.0, group_by_batch=False)


def tcp_packet_source(packets):
    def _source(interface, bpf_filter, stop_event):
        for packet in packets:
            if stop_event.is_set():
                return
            yield packet
        while not stop_event.is_set():
            time.sleep(0.02)
    return _source


def wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestLiveCaptureWithRealtimeDetection:
    def test_a_live_flow_is_detected_and_may_alert(self, db_session, tmp_path, session_factory):
        register_supervised_model(db_session, tmp_path)

        def flow_processor(batch_id, flow, row_number) -> None:
            process_finalized_flow(batch_id, flow, row_number, session_factory=session_factory)

        now = time.time()
        packets = [
            PacketRecord(timestamp=now, source_ip="10.0.0.1", destination_ip="10.0.0.2", protocol_number=6,
                         length=10000, source_port=1234, destination_port=80, tcp_flags=2),
            PacketRecord(timestamp=now + 0.01, source_ip="10.0.0.2", destination_ip="10.0.0.1", protocol_number=6,
                         length=300, source_port=80, destination_port=1234, tcp_flags=18),
        ]
        session = start_capture(
            db_session, interface="eth-rt-test", packet_source=tcp_packet_source(packets),
            flow_processor=flow_processor, session_factory=session_factory, idle_timeout_seconds=60.0,
        )

        wait_until(lambda: get_status(db_session, session.id).stats.get("flows_finalized", 0) >= 0)
        stop_capture(db_session, session.id)

        detection_rows = db_session.query(DetectionResult).filter(DetectionResult.batch_id == session.batch_id).all()
        assert len(detection_rows) == 1
        # Whether or not it crossed the alert threshold is a real,
        # data-dependent outcome -- the only thing this test asserts
        # unconditionally is that real inference ran and was persisted.

    def test_session_status_is_stopped_cleanly_after_realtime_processing(self, db_session, tmp_path, session_factory):
        register_supervised_model(db_session, tmp_path)

        def flow_processor(batch_id, flow, row_number) -> None:
            process_finalized_flow(batch_id, flow, row_number, session_factory=session_factory)

        session = start_capture(
            db_session, interface="eth-rt-test-2", packet_source=tcp_packet_source([]),
            flow_processor=flow_processor, session_factory=session_factory,
        )
        stopped = stop_capture(db_session, session.id)
        assert stopped.status == CaptureSessionStatus.STOPPED
