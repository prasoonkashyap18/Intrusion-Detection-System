"""Tests for the Step 36/37 Alert API:

    GET   /api/v1/alerts
    GET   /api/v1/alerts/{alert_id}
    GET   /api/v1/alerts/{alert_id}/history
    PATCH /api/v1/alerts/{alert_id}/status

Rows are inserted straight into the isolated in-memory database; the
development database is never touched, and nothing here runs CSV
ingestion, feature extraction, or model inference.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_db
from app.main import app
from app.models.alert import Alert
from app.models.detection_batch import DetectionBatch
from app.models.enums import AlertStatus, ProcessingStatus, Severity


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="traffic.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_alert(db_session, batch, **overrides) -> Alert:
    defaults = dict(
        batch_id=batch.id, row_number=1, title="Confirmed attack signal", description="d",
        severity=Severity.HIGH, status=AlertStatus.OPEN, evidence={"hybrid_outcome": "confirmed_attack"},
    )
    alert = Alert(**{**defaults, **overrides})
    db_session.add(alert)
    db_session.commit()
    return alert


class TestListAlerts:
    def test_empty_collection_is_a_normal_200(self, client):
        response = client.get("/api/v1/alerts")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total_items"] == 0
        assert body["total_pages"] == 0

    def test_lists_persisted_alerts(self, client, db_session):
        batch = add_batch(db_session)
        add_alert(db_session, batch)

        response = client.get("/api/v1/alerts")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_filters_by_status(self, client, db_session):
        batch = add_batch(db_session)
        add_alert(db_session, batch, row_number=1, status=AlertStatus.OPEN)
        add_alert(db_session, batch, row_number=2, status=AlertStatus.RESOLVED)

        response = client.get("/api/v1/alerts", params={"status": "resolved"})

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["status"] == "resolved"

    def test_filters_by_severity(self, client, db_session):
        batch = add_batch(db_session)
        add_alert(db_session, batch, row_number=1, severity=Severity.LOW)
        add_alert(db_session, batch, row_number=2, severity=Severity.CRITICAL)

        response = client.get("/api/v1/alerts", params={"severity": "critical"})

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["severity"] == "critical"

    def test_filters_by_batch_id(self, client, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        add_alert(db_session, batch_a)
        add_alert(db_session, batch_b)

        response = client.get("/api/v1/alerts", params={"batch_id": str(batch_a.id)})

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["batch_id"] == str(batch_a.id)

    def test_invalid_sort_by_is_422(self, client):
        response = client.get("/api/v1/alerts", params={"sort_by": "not_a_field"})
        assert response.status_code == 422

    def test_invalid_page_size_is_422(self, client):
        response = client.get("/api/v1/alerts", params={"page_size": 0})
        assert response.status_code == 422


class TestGetAlertDetail:
    def test_returns_the_alert(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        response = client.get(f"/api/v1/alerts/{alert.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(alert.id)
        assert body["evidence"] == {"hybrid_outcome": "confirmed_attack"}

    def test_unknown_id_is_404(self, client):
        response = client.get(f"/api/v1/alerts/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_malformed_id_is_422(self, client):
        response = client.get("/api/v1/alerts/not-a-uuid")
        assert response.status_code == 422


class TestAlertHistoryEndpoint:
    def test_empty_history_is_a_normal_200(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        response = client.get(f"/api/v1/alerts/{alert.id}/history")

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_history_reflects_a_status_update(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        client.patch(f"/api/v1/alerts/{alert.id}/status", json={"status": "acknowledged"})
        response = client.get(f"/api/v1/alerts/{alert.id}/history")

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["previous_status"] == "open"
        assert items[0]["new_status"] == "acknowledged"

    def test_unknown_id_is_404(self, client):
        response = client.get(f"/api/v1/alerts/{uuid.uuid4()}/history")
        assert response.status_code == 404


class TestUpdateAlertStatus:
    def test_valid_transition_succeeds(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        response = client.patch(f"/api/v1/alerts/{alert.id}/status", json={"status": "acknowledged"})

        assert response.status_code == 200
        assert response.json()["status"] == "acknowledged"

    def test_valid_transition_with_note_succeeds(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        response = client.patch(
            f"/api/v1/alerts/{alert.id}/status", json={"status": "false_positive", "note": "benign test data"}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "false_positive"

    def test_invalid_transition_is_409(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, status=AlertStatus.RESOLVED)

        response = client.patch(f"/api/v1/alerts/{alert.id}/status", json={"status": "open"})

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "invalid_alert_transition"

    def test_same_status_is_a_no_op_200(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, status=AlertStatus.OPEN)

        response = client.patch(f"/api/v1/alerts/{alert.id}/status", json={"status": "open"})

        assert response.status_code == 200
        assert response.json()["status"] == "open"

    def test_unknown_id_is_404(self, client):
        response = client.patch(f"/api/v1/alerts/{uuid.uuid4()}/status", json={"status": "acknowledged"})
        assert response.status_code == 404

    def test_missing_status_field_is_422(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        response = client.patch(f"/api/v1/alerts/{alert.id}/status", json={})

        assert response.status_code == 422

    def test_invalid_status_value_is_422(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        response = client.patch(f"/api/v1/alerts/{alert.id}/status", json={"status": "not_a_real_status"})

        assert response.status_code == 422
