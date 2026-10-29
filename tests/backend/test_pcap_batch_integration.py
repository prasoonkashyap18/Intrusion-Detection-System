"""Step 41 end-to-end integration: a real uploaded PCAP capture,
processed through the real `process_batch`/`start_pcap_processing`
entry point, producing real MappedFeatureRecord rows and — when a
compatible model exists — flowing through the entire existing
detection pipeline (inference, hybrid detection, risk scoring,
explainability, alerting) completely unmodified.
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
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.pcap_capture_metadata import PcapCaptureMetadata
from app.services.batch_processor import process_batch, start_pcap_processing, start_processing
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.model_training import train_baseline_model
from app.services.pcap_feature_mapping import PCAP_DATASET_SCHEMA
from app.services.pcap_upload_service import PcapUploadConfig, register_pcap_upload
from app.services.training_data import TrainingDataset

N_FEATURES = len(FEATURE_SCHEMA)
_ETH = dict(src="00:11:22:33:44:55", dst="66:77:88:99:aa:bb")


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "uploads"
    directory.mkdir()
    return directory


def build_pcap_bytes() -> bytes:
    import io as _io

    from scapy.all import IP, TCP, UDP, Ether, PcapWriter

    buffer = _io.BytesIO()
    writer = PcapWriter(buffer, sync=True)
    writer.write(Ether(**_ETH) / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=1111, dport=80, flags="S"))
    writer.write(Ether(**_ETH) / IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=80, dport=1111, flags="SA"))
    writer.write(Ether(**_ETH) / IP(src="10.0.0.3", dst="10.0.0.4") / UDP(sport=53, dport=5353))
    writer.flush()
    return buffer.getvalue()


def upload_pcap(db_session, upload_dir: Path, content: bytes) -> DetectionBatch:
    config = PcapUploadConfig(directory=upload_dir, max_bytes=10_000_000)
    return register_pcap_upload(
        db=db_session, config=config, source=__import__("io").BytesIO(content),
        filename="live_traffic.pcap", content_type="application/octet-stream",
    )


def register_ready_pcap_model(db_session, tmp_path: Path):
    """Trains a tiny baseline model with dataset_name=PCAP_DATASET_SCHEMA
    — the only way a supervised model is ever considered compatible
    with packet-derived flows (see app.services.pcap_feature_mapping's
    module docstring on why an NSL-KDD-style model is NOT reused here)."""
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
    dataset = TrainingDataset(
        feature_matrix=feature_matrix, labels=labels, record_ids=record_ids, batch_ids=batch_ids,
        dataset_schemas=[PCAP_DATASET_SCHEMA] * 40, feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )
    return train_baseline_model(
        db_session, dataset, model_name="pcap-baseline", artifact_dir=tmp_path,
        test_fraction=0.0, group_by_batch=False,
    )


class TestPcapBatchProcessingWithoutAModel:
    def test_flows_persist_even_with_no_compatible_model(self, db_session, upload_dir):
        batch = upload_pcap(db_session, upload_dir, build_pcap_bytes())

        result = process_batch(db_session, batch.id, upload_dir)

        # No model is registered for "pcap-flow" -- this mirrors the
        # CSV path's own Step 26 behavior exactly: a recognized/derivable
        # schema with no compatible trained model is a processing
        # failure, not a silent skip (see _run_model_inference).
        assert result.status == ProcessingStatus.FAILED
        # Feature persistence itself already committed before inference
        # ran (the same "commit features, then attempt inference"
        # sequencing the CSV path already uses) -- a failure to find a
        # compatible model fails the batch but does not retroactively
        # erase the flows that were genuinely parsed and persisted.
        rows = db_session.scalars(select(MappedFeatureRecord).where(MappedFeatureRecord.batch_id == batch.id)).all()
        assert len(rows) == 2

    def test_metadata_still_has_no_populated_stats_before_any_attempt(self, db_session, upload_dir):
        batch = upload_pcap(db_session, upload_dir, build_pcap_bytes())
        metadata = db_session.scalars(select(PcapCaptureMetadata).where(PcapCaptureMetadata.batch_id == batch.id)).first()
        assert metadata.packets_read is None
        assert metadata.flows_constructed is None


class TestPcapBatchProcessingWithAModel:
    def test_full_pipeline_runs_through_existing_detection_stack(self, db_session, upload_dir, tmp_path):
        register_ready_pcap_model(db_session, tmp_path)
        batch = upload_pcap(db_session, upload_dir, build_pcap_bytes())

        result = process_batch(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        detection_rows = db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch.id)).all()
        assert len(detection_rows) == 2  # two independent flows (TCP handshake pair + UDP)

    def test_pcap_metadata_stats_are_populated_after_processing(self, db_session, upload_dir, tmp_path):
        register_ready_pcap_model(db_session, tmp_path)
        batch = upload_pcap(db_session, upload_dir, build_pcap_bytes())

        process_batch(db_session, batch.id, upload_dir)

        metadata = db_session.scalars(select(PcapCaptureMetadata).where(PcapCaptureMetadata.batch_id == batch.id)).first()
        assert metadata.packets_read == 3
        assert metadata.flows_constructed == 2

    def test_dispatch_correctly_routes_a_pcap_batch_not_through_plain_start_processing(self, db_session, upload_dir, tmp_path):
        register_ready_pcap_model(db_session, tmp_path)
        batch = upload_pcap(db_session, upload_dir, build_pcap_bytes())

        # Calling plain start_processing (the CSV entry point) on a PCAP
        # batch must fail safely -- there is no <batch_id>.csv file --
        # proving the dispatcher (process_batch) is what makes this work,
        # not an accidental compatibility between the two paths.
        result = start_processing(db_session, batch.id, upload_dir)
        assert result.status == ProcessingStatus.FAILED


class TestCsvProcessingUnaffected:
    def test_a_csv_batch_has_no_pcap_metadata_and_uses_start_processing(self, db_session, upload_dir):
        from app.services.upload_service import UploadConfig, register_upload

        csv_bytes = b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n5,tcp,http,SF,100,200,normal\n"
        config = UploadConfig(directory=upload_dir, max_bytes=10_000_000)
        batch = register_upload(
            db=db_session, config=config, source=__import__("io").BytesIO(csv_bytes),
            filename="traffic.csv", content_type="text/csv",
        )

        metadata = db_session.scalars(select(PcapCaptureMetadata).where(PcapCaptureMetadata.batch_id == batch.id)).first()
        assert metadata is None

        result = process_batch(db_session, batch.id, upload_dir)
        # No compatible nsl-kdd-style model registered either -- still
        # proves the dispatch chose the CSV path (it recognized the
        # schema and tried inference), not a crash from missing PCAP
        # metadata.
        assert result.status == ProcessingStatus.FAILED
