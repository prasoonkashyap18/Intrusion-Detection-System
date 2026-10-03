"""Tests for GET /api/v1/detection/batches/{batch_id} (single persisted batch).

Batches are inserted straight into the isolated in-memory database; the
development database is never touched.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.database import get_db
from app.main import app
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus

BASE_TIME = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)


def url_for(batch_id) -> str:
    return f"/api/v1/detection/batches/{batch_id}"


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(
        id=uuid.uuid4(),
        filename="traffic.csv",
        status=ProcessingStatus.PENDING,
        total_records=1234,
        processed_records=0,
        failed_records=0,
        created_at=BASE_TIME,
    )
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


class TestRetrieval:
    def test_returns_the_requested_batch(self, client, db_session):
        batch = add_batch(db_session, filename="traffic.csv", total_records=1234)

        response = client.get(url_for(batch.id))

        assert response.status_code == 200
        assert response.json() == {
            "batch_id": str(batch.id),
            "filename": "traffic.csv",
            "status": "pending",
            "total_records": 1234,
            "processed_records": 0,
            "failed_records": 0,
            "created_at": "2026-10-03T08:00:00Z",
            "completed_at": None,
        }

    def test_returns_a_completed_batch_with_its_completion_time(self, client, db_session):
        batch = add_batch(
            db_session,
            status=ProcessingStatus.COMPLETED,
            processed_records=1234,
            completed_at=BASE_TIME + timedelta(minutes=5),
        )

        body = client.get(url_for(batch.id)).json()

        assert body["status"] == "completed"
        assert body["processed_records"] == 1234
        assert body["completed_at"] == "2026-10-03T08:05:00Z"

    def test_returns_a_failed_batch_as_stored(self, client, db_session):
        batch = add_batch(db_session, status=ProcessingStatus.FAILED, failed_records=1234, error_message="internal detail")

        body = client.get(url_for(batch.id)).json()

        assert body["status"] == "failed"
        assert body["failed_records"] == 1234

    def test_returns_a_processing_batch_as_stored(self, client, db_session):
        batch = add_batch(db_session, status=ProcessingStatus.PROCESSING)

        assert client.get(url_for(batch.id)).json()["status"] == "processing"

    def test_fetches_the_correct_batch_among_several(self, client, db_session):
        add_batch(db_session, filename="other-1.csv")
        target = add_batch(db_session, filename="target.csv")
        add_batch(db_session, filename="other-2.csv")

        body = client.get(url_for(target.id)).json()

        assert body["batch_id"] == str(target.id)
        assert body["filename"] == "target.csv"

    def test_does_not_change_the_batch_status(self, client, db_session):
        batch = add_batch(db_session)

        client.get(url_for(batch.id))
        db_session.expire_all()

        assert db_session.get(DetectionBatch, batch.id).status == ProcessingStatus.PENDING


class TestNotFound:
    def test_unknown_but_well_formed_id_returns_404(self, client):
        response = client.get(url_for(uuid.uuid4()))

        assert response.status_code == 404
        assert response.json() == {"error": "batch_not_found", "message": "No batch was found with that ID."}

    def test_404_is_returned_even_when_other_batches_exist(self, client, db_session):
        add_batch(db_session)

        response = client.get(url_for(uuid.uuid4()))

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "malformed",
        ["not-a-uuid", "12345", "3f2b8c1e-6a4d-4e0b-9d7a", "3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30extra"],
    )
    def test_malformed_id_is_rejected_with_422_not_404(self, client, malformed):
        response = client.get(url_for(malformed))

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    def test_empty_id_segment_falls_through_to_the_list_endpoint(self, client):
        # /batches/ (trailing slash, empty id) does not match {batch_id} at
        # all, so it redirects to the list route rather than entering this
        # endpoint's validation or lookup.
        response = client.get("/api/v1/detection/batches/", follow_redirects=True)

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_dot_segments_cannot_escape_the_batches_path(self, client):
        # The HTTP client normalizes ../ segments before the request is sent
        # (RFC 3986), so the server never receives a traversal attempt here;
        # this confirms the resulting path simply matches no route.
        response = client.get("/api/v1/detection/batches/../../etc/passwd")

        assert response.status_code == 404
        assert "passwd" not in response.text


class TestSecurity:
    def test_response_exposes_only_the_documented_fields(self, client, db_session):
        batch = add_batch(db_session)

        body = client.get(url_for(batch.id)).json()

        assert set(body) == {
            "batch_id",
            "filename",
            "status",
            "total_records",
            "processed_records",
            "failed_records",
            "created_at",
            "completed_at",
        }

    def test_does_not_expose_the_error_message_field(self, client, db_session):
        batch = add_batch(db_session, status=ProcessingStatus.FAILED, error_message="Traceback: /srv/secret/path")

        response = client.get(url_for(batch.id))

        assert "Traceback" not in response.text
        assert "error_message" not in response.text

    def test_database_error_returns_a_clean_500(self, client, db_session, monkeypatch):
        def failing_get(*args, **kwargs):
            raise OperationalError("SELECT * FROM detection_batches", {}, Exception("disk I/O error at C:\\secret"))

        monkeypatch.setattr(db_session, "get", failing_get)

        response = client.get(url_for(uuid.uuid4()))

        assert response.status_code == 500
        assert response.json() == {"error": "batch_unavailable", "message": "Unable to load the detection batch."}
        assert "secret" not in response.text
        assert "SELECT" not in response.text
