"""Tests for GET /api/v1/dashboard/overview (Step 45): real, measured
counts, never fabricated, available to any authenticated role.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_db
from app.main import app
from app.models.alert import Alert
from app.models.anomaly_result import AnomalyResult
from app.models.capture_session import CaptureSession
from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.model_metadata import ModelMetadata
from app.models.enums import AlertStatus, CaptureSessionStatus, ProcessingStatus, Severity


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_model(db_session, **overrides) -> ModelMetadata:
    defaults = dict(id=uuid.uuid4(), model_name="m", model_version="v1", model_type="random_forest")
    model = ModelMetadata(**{**defaults, **overrides})
    db_session.add(model)
    db_session.commit()
    return model


def add_detection_result(db_session, batch, model, **overrides) -> DetectionResult:
    defaults = dict(batch_id=batch.id, model_id=model.id, row_number=1, predicted_label=0, prediction_name="benign")
    row = DetectionResult(**{**defaults, **overrides})
    db_session.add(row)
    db_session.commit()
    return row


def add_anomaly_result(db_session, batch, model, **overrides) -> AnomalyResult:
    defaults = dict(batch_id=batch.id, model_id=model.id, row_number=1, is_anomaly=False, anomaly_score=0.1)
    row = AnomalyResult(**{**defaults, **overrides})
    db_session.add(row)
    db_session.commit()
    return row


def add_alert(db_session, batch, **overrides) -> Alert:
    defaults = dict(
        batch_id=batch.id, row_number=1, title="t", description="d", severity=Severity.HIGH,
        status=AlertStatus.OPEN, evidence={},
    )
    alert = Alert(**{**defaults, **overrides})
    db_session.add(alert)
    db_session.commit()
    return alert


class TestDashboardOverview:
    def test_empty_system_returns_real_zeros(self, client):
        response = client.get("/api/v1/dashboard/overview")
        assert response.status_code == 200
        body = response.json()
        assert body["total_batches"] == 0
        assert body["total_alerts"] == 0
        assert body["open_alerts"] == 0
        assert body["alert_severity_distribution"] == {"low": 0, "medium": 0, "high": 0, "critical": 0}
        assert body["active_capture_sessions"] == 0

    def test_counts_reflect_real_persisted_rows(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_detection_result(db_session, batch, model)
        add_anomaly_result(db_session, batch, model)
        add_alert(db_session, batch, severity=Severity.CRITICAL, status=AlertStatus.OPEN)
        add_alert(db_session, batch, row_number=2, severity=Severity.LOW, status=AlertStatus.RESOLVED)

        response = client.get("/api/v1/dashboard/overview")
        body = response.json()

        assert body["total_batches"] == 1
        assert body["total_detection_results"] == 1
        assert body["total_anomaly_results"] == 1
        assert body["total_alerts"] == 2
        assert body["open_alerts"] == 1  # only the OPEN one, not the RESOLVED one
        assert body["alert_severity_distribution"]["critical"] == 1
        assert body["alert_severity_distribution"]["low"] == 1

    def test_open_alerts_includes_acknowledged_and_investigating(self, client, db_session):
        batch = add_batch(db_session)
        add_alert(db_session, batch, row_number=1, status=AlertStatus.ACKNOWLEDGED)
        add_alert(db_session, batch, row_number=2, status=AlertStatus.INVESTIGATING)
        add_alert(db_session, batch, row_number=3, status=AlertStatus.FALSE_POSITIVE)

        response = client.get("/api/v1/dashboard/overview")
        assert response.json()["open_alerts"] == 2

    def test_active_capture_sessions_counts_only_running(self, client, db_session):
        batch = add_batch(db_session)
        db_session.add(CaptureSession(
            id=uuid.uuid4(), batch_id=batch.id, interface_name="eth0", idle_timeout_seconds=120.0,
            status=CaptureSessionStatus.RUNNING, stats={},
        ))
        db_session.add(CaptureSession(
            id=uuid.uuid4(), batch_id=batch.id, interface_name="eth1", idle_timeout_seconds=120.0,
            status=CaptureSessionStatus.STOPPED, stats={},
        ))
        db_session.commit()

        response = client.get("/api/v1/dashboard/overview")
        assert response.json()["active_capture_sessions"] == 1


class TestDashboardAuth:
    def test_requires_authentication(self, db_session):
        from app.core.security import get_current_user

        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides.pop(get_current_user, None)
        client = TestClient(app)
        try:
            response = client.get("/api/v1/dashboard/overview")
            assert response.status_code == 401
        finally:
            app.dependency_overrides.clear()

    def test_read_only_viewer_can_view_the_dashboard(self, db_session):
        from app.core.security import get_current_user
        from app.models.enums import UserRole

        class _Viewer:
            id = uuid.uuid4()
            username = "viewer"
            role = UserRole.READ_ONLY_VIEWER
            is_active = True

        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: _Viewer()
        client = TestClient(app)
        try:
            response = client.get("/api/v1/dashboard/overview")
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()
