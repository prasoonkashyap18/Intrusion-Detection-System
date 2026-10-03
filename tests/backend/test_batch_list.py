"""Tests for GET /api/v1/detection/batches (persisted batch retrieval).

Batches are inserted straight into the isolated in-memory database; the
development database is never touched.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from app.db.database import get_db
from app.main import app
from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus

URL = "/api/v1/detection/batches"
BASE_TIME = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_batch(db_session, index: int, *, status=ProcessingStatus.PENDING, records: int = 10, **extra) -> DetectionBatch:
    """Batch `index` is created `index` minutes after BASE_TIME, so a higher index is newer."""
    batch = DetectionBatch(
        id=uuid.uuid4(),
        filename=extra.pop("filename", f"batch-{index}.csv"),
        status=status,
        total_records=records,
        processed_records=extra.pop("processed_records", 0),
        failed_records=extra.pop("failed_records", 0),
        created_at=BASE_TIME + timedelta(minutes=index),
        **extra,
    )
    db_session.add(batch)
    db_session.commit()
    return batch


def seed(db_session, count: int) -> list[DetectionBatch]:
    return [add_batch(db_session, i) for i in range(count)]


class TestEmptyCollection:
    def test_returns_200_not_404(self, client):
        assert client.get(URL).status_code == 200

    def test_returns_an_empty_list_with_correct_metadata(self, client):
        assert client.get(URL).json() == {"items": [], "page": 1, "page_size": 20, "total_items": 0, "total_pages": 0}


class TestListing:
    def test_returns_multiple_batches(self, client, db_session):
        seed(db_session, 3)

        body = client.get(URL).json()

        assert len(body["items"]) == 3
        assert body["total_items"] == 3

    def test_orders_newest_first(self, client, db_session):
        seed(db_session, 5)

        names = [item["filename"] for item in client.get(URL).json()["items"]]

        assert names == [f"batch-{i}.csv" for i in (4, 3, 2, 1, 0)]

    def test_batches_created_in_the_same_instant_still_order_deterministically(self, client, db_session):
        for _ in range(4):
            add_batch(db_session, 0)

        first = [item["batch_id"] for item in client.get(URL).json()["items"]]
        second = [item["batch_id"] for item in client.get(URL).json()["items"]]

        assert first == second
        assert len(set(first)) == 4

    def test_item_shape_and_pending_status(self, client, db_session):
        batch = add_batch(db_session, 0, records=1234)

        item = client.get(URL).json()["items"][0]

        assert item == {
            "batch_id": str(batch.id),
            "filename": "batch-0.csv",
            "status": "pending",
            "total_records": 1234,
            "processed_records": 0,
            "failed_records": 0,
            "created_at": "2026-10-03T08:00:00Z",
            "completed_at": None,
        }

    def test_returns_completed_and_failed_batches_as_stored(self, client, db_session):
        add_batch(
            db_session,
            0,
            status=ProcessingStatus.COMPLETED,
            processed_records=10,
            completed_at=BASE_TIME + timedelta(minutes=5),
        )
        add_batch(db_session, 1, status=ProcessingStatus.FAILED, failed_records=10, error_message="internal detail")
        add_batch(db_session, 2, status=ProcessingStatus.PROCESSING)

        by_name = {item["filename"]: item for item in client.get(URL).json()["items"]}

        assert by_name["batch-0.csv"]["status"] == "completed"
        assert by_name["batch-0.csv"]["completed_at"] == "2026-10-03T08:05:00Z"
        assert by_name["batch-1.csv"]["status"] == "failed"
        assert by_name["batch-1.csv"]["failed_records"] == 10
        assert by_name["batch-2.csv"]["status"] == "processing"

    def test_does_not_expose_error_messages(self, client, db_session):
        add_batch(db_session, 0, status=ProcessingStatus.FAILED, error_message="Traceback: /srv/secret/path")

        assert "Traceback" not in client.get(URL).text

    def test_does_not_change_batch_status(self, client, db_session):
        add_batch(db_session, 0)

        client.get(URL)
        db_session.expire_all()

        assert db_session.scalars(select(DetectionBatch)).one().status == ProcessingStatus.PENDING

    def test_creates_no_detection_results(self, client, db_session):
        seed(db_session, 3)

        client.get(URL)

        assert db_session.scalar(select(func.count()).select_from(DetectionResult)) == 0


class TestPagination:
    def test_default_page_and_size(self, client, db_session):
        seed(db_session, 25)

        body = client.get(URL).json()

        assert (body["page"], body["page_size"], body["total_items"], body["total_pages"]) == (1, 20, 25, 2)
        assert len(body["items"]) == 20

    def test_page_one(self, client, db_session):
        seed(db_session, 5)

        body = client.get(URL, params={"page": 1, "page_size": 2}).json()

        assert [i["filename"] for i in body["items"]] == ["batch-4.csv", "batch-3.csv"]
        assert body["total_pages"] == 3

    def test_page_two_continues_without_overlap(self, client, db_session):
        seed(db_session, 5)

        body = client.get(URL, params={"page": 2, "page_size": 2}).json()

        assert [i["filename"] for i in body["items"]] == ["batch-2.csv", "batch-1.csv"]
        assert body["page"] == 2

    def test_last_page_holds_the_remainder(self, client, db_session):
        seed(db_session, 5)

        body = client.get(URL, params={"page": 3, "page_size": 2}).json()

        assert [i["filename"] for i in body["items"]] == ["batch-0.csv"]

    def test_page_size_is_respected(self, client, db_session):
        seed(db_session, 8)

        assert len(client.get(URL, params={"page_size": 3}).json()["items"]) == 3

    def test_total_pages_rounds_up_and_handles_exact_multiples(self, client, db_session):
        seed(db_session, 4)

        assert client.get(URL, params={"page_size": 2}).json()["total_pages"] == 2
        assert client.get(URL, params={"page_size": 3}).json()["total_pages"] == 2

    def test_page_beyond_the_end_is_an_empty_200_with_real_totals(self, client, db_session):
        seed(db_session, 3)

        response = client.get(URL, params={"page": 9, "page_size": 2})

        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total_items"] == 3

    def test_maximum_page_size_is_accepted(self, client):
        assert client.get(URL, params={"page_size": 100}).status_code == 200

    @pytest.mark.parametrize("page_size", [101, 1000, 10_000_000])
    def test_page_size_above_the_maximum_is_rejected(self, client, page_size):
        assert client.get(URL, params={"page_size": page_size}).status_code == 422

    @pytest.mark.parametrize("page", [0, -1, "abc", "1.5", 1_000_001])
    def test_invalid_page_is_rejected(self, client, page):
        response = client.get(URL, params={"page": page})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    @pytest.mark.parametrize("page_size", [0, -5, "abc", ""])
    def test_invalid_page_size_is_rejected(self, client, page_size):
        assert client.get(URL, params={"page_size": page_size}).status_code == 422


class TestSecurityAndFailures:
    def test_response_exposes_only_the_documented_fields(self, client, db_session):
        add_batch(db_session, 0)

        response = client.get(URL)

        assert set(response.json()["items"][0]) == {
            "batch_id",
            "filename",
            "status",
            "total_records",
            "processed_records",
            "failed_records",
            "created_at",
            "completed_at",
        }
        assert "path" not in response.text.lower()

    def test_database_error_returns_a_clean_500(self, client, db_session, monkeypatch):
        def failing_scalar(*args, **kwargs):
            raise OperationalError(
                "SELECT secret_column FROM detection_batches", {}, Exception("disk I/O error at C:\\secret")
            )

        monkeypatch.setattr(db_session, "scalar", failing_scalar)

        response = client.get(URL)

        assert response.status_code == 500
        assert response.json() == {"error": "batches_unavailable", "message": "Unable to load detection batches."}
        assert "secret" not in response.text
        assert "SELECT" not in response.text

    def test_listing_runs_exactly_two_queries_and_loads_no_relationships(self, client, db_session):
        seed(db_session, 3)
        statements: list[str] = []
        engine = db_session.get_bind()

        def record(conn, cursor, statement, *args):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            client.get(URL)
        finally:
            event.remove(engine, "before_cursor_execute", record)

        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 2  # one COUNT and one page: no per-row queries
        assert not any("detection_results" in s for s in selects)
