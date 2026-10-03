"""Tests for POST /api/v1/detection/batches/{batch_id}/process and the
batch-processing service (app.services.batch_processor).

Every test uses the isolated in-memory database from conftest.py and a
temporary upload directory — never the development database or
backend/data/uploads/. No test asserts that analysis happened: this step
only establishes the pending -> processing boundary.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.detection import get_upload_config
from app.db.database import get_db
from app.main import app
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.services import batch_processor
from app.services.batch_processor import claim_for_processing, start_processing
from app.services.upload_service import UploadConfig


def url_for(batch_id) -> str:
    return f"/api/v1/detection/batches/{batch_id}/process"


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "uploads"
    directory.mkdir()
    return directory


@pytest.fixture()
def client(db_session, upload_dir: Path):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_upload_config] = lambda: UploadConfig(directory=upload_dir, max_bytes=1024 * 1024)
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(
        id=uuid.uuid4(),
        filename="traffic.csv",
        status=ProcessingStatus.PENDING,
        total_records=10,
        processed_records=0,
        failed_records=0,
    )
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def with_uploaded_file(upload_dir: Path, batch: DetectionBatch, content: bytes = b"a,b\n1,2\n") -> DetectionBatch:
    (upload_dir / f"{batch.id}.csv").write_bytes(content)
    return batch


class TestNotFound:
    def test_nonexistent_batch_returns_404(self, client):
        response = client.post(url_for(uuid.uuid4()))

        assert response.status_code == 404
        assert response.json() == {"error": "batch_not_found", "message": "No batch was found with that ID."}

    @pytest.mark.parametrize("malformed", ["not-a-uuid", "12345", "3f2b8c1e-6a4d-4e0b-9d7a"])
    def test_malformed_id_is_rejected_with_422_not_404(self, client, malformed):
        response = client.post(f"/api/v1/detection/batches/{malformed}/process")

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"


class TestSuccessfulStart:
    def test_pending_batch_transitions_to_processing(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session))

        response = client.post(url_for(batch.id))

        assert response.status_code == 200
        assert response.json() == {
            "batch_id": str(batch.id),
            "status": "processing",
            "message": "Batch processing started.",
        }
        db_session.expire_all()
        assert db_session.get(DetectionBatch, batch.id).status == ProcessingStatus.PROCESSING

    def test_response_schema_has_exactly_the_documented_fields(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session))

        body = client.post(url_for(batch.id)).json()

        assert set(body) == {"batch_id", "status", "message"}

    def test_counters_stay_truthful_when_processing_starts(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session, total_records=500))

        client.post(url_for(batch.id))

        db_session.expire_all()
        stored = db_session.get(DetectionBatch, batch.id)
        assert stored.total_records == 500
        assert stored.processed_records == 0
        assert stored.failed_records == 0

    def test_does_not_create_detection_results(self, client, db_session, upload_dir):
        from sqlalchemy import func, select

        from app.models.detection_result import DetectionResult

        batch = with_uploaded_file(upload_dir, add_batch(db_session))

        client.post(url_for(batch.id))

        assert db_session.scalar(select(func.count()).select_from(DetectionResult)) == 0


class TestInvalidTransitions:
    @pytest.mark.parametrize("status", [ProcessingStatus.PROCESSING, ProcessingStatus.COMPLETED, ProcessingStatus.FAILED])
    def test_non_pending_batch_cannot_be_started(self, client, db_session, status):
        batch = add_batch(db_session, status=status)

        response = client.post(url_for(batch.id))

        assert response.status_code == 409
        assert response.json()["error"] == "invalid_batch_state"

    def test_non_pending_batch_is_left_unchanged(self, client, db_session):
        batch = add_batch(db_session, status=ProcessingStatus.COMPLETED, processed_records=7)

        client.post(url_for(batch.id))

        db_session.expire_all()
        stored = db_session.get(DetectionBatch, batch.id)
        assert stored.status == ProcessingStatus.COMPLETED
        assert stored.processed_records == 7

    def test_processing_cannot_be_claimed_twice_through_the_api(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session))

        first = client.post(url_for(batch.id))
        second = client.post(url_for(batch.id))

        assert first.status_code == 200
        assert second.status_code == 409


class TestMissingUploadFile:
    def test_batch_is_marked_failed_when_its_file_is_missing(self, client, db_session):
        # No with_uploaded_file(): the batch row exists but its CSV does not.
        batch = add_batch(db_session)

        response = client.post(url_for(batch.id))

        assert response.status_code == 200
        assert response.json() == {
            "batch_id": str(batch.id),
            "status": "failed",
            "message": "Batch processing could not be started.",
        }

    def test_failure_records_a_completion_time_and_truthful_counters(self, client, db_session):
        batch = add_batch(db_session, total_records=42)

        client.post(url_for(batch.id))

        db_session.expire_all()
        stored = db_session.get(DetectionBatch, batch.id)
        assert stored.status == ProcessingStatus.FAILED
        assert stored.completed_at is not None
        assert stored.total_records == 42
        assert stored.processed_records == 0
        assert stored.failed_records == 0

    def test_error_message_does_not_expose_the_filesystem_path(self, client, db_session, upload_dir):
        batch = add_batch(db_session)

        response = client.post(url_for(batch.id))

        assert str(upload_dir) not in response.text
        assert str(batch.id) + ".csv" not in response.text
        assert ".csv" not in response.text


class TestUnexpectedProcessingFailure:
    def test_unexpected_exception_marks_the_batch_failed_without_leaking_details(
        self, client, db_session, upload_dir, monkeypatch
    ):
        def boom(_csv_path):
            raise RuntimeError("disk I/O error at C:\\secret\\path\\traffic.csv")

        monkeypatch.setattr(batch_processor, "_run_feature_extraction", boom)
        batch = with_uploaded_file(upload_dir, add_batch(db_session))

        response = client.post(url_for(batch.id))

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        assert "secret" not in response.text
        assert "RuntimeError" not in response.text
        assert "Traceback" not in response.text

        db_session.expire_all()
        stored = db_session.get(DetectionBatch, batch.id)
        assert stored.status == ProcessingStatus.FAILED
        assert stored.error_message is not None
        assert "secret" not in stored.error_message


class TestCsvIngestionIntegration:
    """Confirms Step 14's processing boundary actually calls Step 15's
    ingestion service rather than a no-op — both the success and failure
    paths observable through the public endpoint."""

    def test_a_multi_row_csv_ingests_cleanly_and_stays_processing(self, client, db_session, upload_dir):
        batch = with_uploaded_file(
            upload_dir,
            add_batch(db_session),
            content=b"source_ip,destination_ip,source_port\n10.0.0.1,10.0.0.2,80\n10.0.0.3,10.0.0.4,443\n",
        )

        response = client.post(url_for(batch.id))

        assert response.json()["status"] == "processing"
        db_session.expire_all()
        assert db_session.get(DetectionBatch, batch.id).status == ProcessingStatus.PROCESSING

    def test_a_structurally_malformed_csv_fails_processing_truthfully(self, client, db_session, upload_dir):
        # Row 2 has 3 columns where the header has 2 — the exact structural
        # defect app.services.ingestion rejects.
        batch = with_uploaded_file(upload_dir, add_batch(db_session), content=b"a,b\n1,2\n3,4,5\n")

        response = client.post(url_for(batch.id))

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        db_session.expire_all()
        stored = db_session.get(DetectionBatch, batch.id)
        assert stored.status == ProcessingStatus.FAILED
        assert stored.error_message is not None
        assert "columns" in stored.error_message

    def test_ingestion_failure_does_not_leak_the_filesystem_path(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), content=b"a,b\n1,2\n3,4,5\n")

        response = client.post(url_for(batch.id))

        assert str(upload_dir) not in response.text
        assert ".csv" not in response.text
        assert ".csv" not in response.text


class TestConcurrentClaim:
    def test_claim_for_processing_cannot_succeed_twice(self, db_session):
        batch = add_batch(db_session)

        first = claim_for_processing(db_session, batch.id)
        second = claim_for_processing(db_session, batch.id)

        assert first is True
        assert second is False
        db_session.expire_all()
        assert db_session.get(DetectionBatch, batch.id).status == ProcessingStatus.PROCESSING

    def test_claim_for_processing_returns_false_for_a_nonexistent_batch(self, db_session):
        assert claim_for_processing(db_session, uuid.uuid4()) is False

    def test_start_processing_raises_409_when_claim_loses_the_race(self, db_session, tmp_path, monkeypatch):
        batch = add_batch(db_session)
        # Simulate a concurrent winner: by the time our UPDATE runs, the row
        # is no longer pending, even though our initial status read above
        # (inside start_processing) still saw `pending`.
        real_claim = batch_processor.claim_for_processing

        def claim_after_a_concurrent_winner(db, batch_id):
            db.execute(
                DetectionBatch.__table__.update()
                .where(DetectionBatch.id == batch_id)
                .values(status=ProcessingStatus.PROCESSING)
            )
            db.commit()
            return real_claim(db, batch_id)

        monkeypatch.setattr(batch_processor, "claim_for_processing", claim_after_a_concurrent_winner)

        from app.core.errors import ApiException

        with pytest.raises(ApiException) as excinfo:
            start_processing(db_session, batch.id, tmp_path)

        assert excinfo.value.status_code == 409
        assert excinfo.value.error == "invalid_batch_state"
