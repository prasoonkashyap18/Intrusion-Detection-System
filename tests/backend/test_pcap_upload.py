"""Tests for app.services.pcap_upload_service and the
POST /api/v1/detection/pcap/upload endpoint: content-based validation,
storage, size limits, and safe filename handling.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.detection import get_pcap_upload_config
from app.db.database import get_db
from app.main import app
from app.models.detection_batch import DetectionBatch
from app.models.pcap_capture_metadata import PcapCaptureMetadata
from app.services.pcap_upload_service import PcapUploadConfig, register_pcap_upload

_ETH = dict(src="00:11:22:33:44:55", dst="66:77:88:99:aa:bb")


def _build_pcap_bytes(tmp_path) -> bytes:
    from scapy.all import IP, TCP, wrpcap, Ether

    path = tmp_path / "seed.pcap"
    wrpcap(str(path), [Ether(**_ETH) / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=1, dport=2)])
    return path.read_bytes()


@pytest.fixture()
def client(db_session, tmp_path):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_pcap_upload_config] = lambda: PcapUploadConfig(
        directory=tmp_path / "uploads", max_bytes=10_000_000
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestRegisterPcapUploadService:
    def test_valid_capture_registers_a_pending_batch_and_metadata(self, db_session, tmp_path):
        content = _build_pcap_bytes(tmp_path)
        config = PcapUploadConfig(directory=tmp_path / "uploads", max_bytes=10_000_000)

        batch = register_pcap_upload(
            db=db_session, config=config, source=io.BytesIO(content), filename="traffic.pcap", content_type="application/octet-stream"
        )

        assert batch.status.value == "pending"
        assert batch.total_records == 0
        metadata = db_session.scalars(select(PcapCaptureMetadata).where(PcapCaptureMetadata.batch_id == batch.id)).first()
        assert metadata is not None
        assert metadata.capture_format == "pcap"
        assert metadata.original_filename == "traffic.pcap"

    def test_wrong_extension_is_rejected(self, db_session, tmp_path):
        config = PcapUploadConfig(directory=tmp_path / "uploads", max_bytes=10_000_000)
        with pytest.raises(Exception) as exc_info:
            register_pcap_upload(
                db=db_session, config=config, source=io.BytesIO(b"whatever"), filename="traffic.csv", content_type="text/csv"
            )
        assert exc_info.value.status_code == 415

    def test_content_not_matching_pcap_magic_is_rejected_despite_correct_extension(self, db_session, tmp_path):
        config = PcapUploadConfig(directory=tmp_path / "uploads", max_bytes=10_000_000)
        with pytest.raises(Exception) as exc_info:
            register_pcap_upload(
                db=db_session, config=config, source=io.BytesIO(b"not a real capture" * 3),
                filename="traffic.pcap", content_type="application/octet-stream",
            )
        assert exc_info.value.status_code == 415

    def test_empty_file_is_rejected(self, db_session, tmp_path):
        config = PcapUploadConfig(directory=tmp_path / "uploads", max_bytes=10_000_000)
        with pytest.raises(Exception) as exc_info:
            register_pcap_upload(
                db=db_session, config=config, source=io.BytesIO(b""), filename="traffic.pcap", content_type="application/octet-stream"
            )
        assert exc_info.value.status_code == 400

    def test_oversized_file_is_rejected_and_discarded(self, db_session, tmp_path):
        content = _build_pcap_bytes(tmp_path) * 1000
        upload_dir = tmp_path / "uploads"
        config = PcapUploadConfig(directory=upload_dir, max_bytes=100)

        with pytest.raises(Exception) as exc_info:
            register_pcap_upload(
                db=db_session, config=config, source=io.BytesIO(content), filename="big.pcap", content_type="application/octet-stream"
            )
        assert exc_info.value.status_code == 413
        assert list(upload_dir.glob("*")) == [] if upload_dir.exists() else True

    def test_rejected_upload_creates_no_batch_row(self, db_session, tmp_path):
        config = PcapUploadConfig(directory=tmp_path / "uploads", max_bytes=10_000_000)
        before = db_session.query(DetectionBatch).count()
        try:
            register_pcap_upload(
                db=db_session, config=config, source=io.BytesIO(b""), filename="traffic.pcap", content_type="application/octet-stream"
            )
        except Exception:
            pass
        assert db_session.query(DetectionBatch).count() == before

    def test_path_traversal_filename_is_sanitized(self, db_session, tmp_path):
        content = _build_pcap_bytes(tmp_path)
        config = PcapUploadConfig(directory=tmp_path / "uploads", max_bytes=10_000_000)

        batch = register_pcap_upload(
            db=db_session, config=config, source=io.BytesIO(content),
            filename="../../etc/evil.pcap", content_type="application/octet-stream",
        )
        assert "/" not in batch.filename and "\\" not in batch.filename
        # Stored under the batch's own generated UUID name, never the
        # client-supplied path.
        stored_files = list((tmp_path / "uploads").glob(f"{batch.id}*"))
        assert len(stored_files) == 1


class TestPcapUploadEndpoint:
    def test_upload_returns_201_and_a_pending_batch(self, client, tmp_path):
        content = _build_pcap_bytes(tmp_path)
        response = client.post(
            "/api/v1/detection/pcap/upload", files={"file": ("traffic.pcap", content, "application/octet-stream")}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["total_records"] == 0

    def test_missing_file_is_400(self, client):
        response = client.post("/api/v1/detection/pcap/upload")
        assert response.status_code == 400

    def test_non_capture_content_is_415(self, client):
        response = client.post(
            "/api/v1/detection/pcap/upload",
            files={"file": ("traffic.pcap", b"not a real capture" * 3, "application/octet-stream")},
        )
        assert response.status_code == 415

    def test_wrong_extension_is_415(self, client, tmp_path):
        content = _build_pcap_bytes(tmp_path)
        response = client.post(
            "/api/v1/detection/pcap/upload", files={"file": ("traffic.txt", content, "text/plain")}
        )
        assert response.status_code == 415
