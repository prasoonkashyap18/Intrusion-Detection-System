"""Role-based access control spot checks across the endpoints Step 46
protects: detection (CSV/PCAP upload, processing), alerts (status
update), and capture (start/stop). Confirms a READ_ONLY_VIEWER can read
but never write/control, and a SECURITY_ANALYST can do the operational
actions a READ_ONLY_VIEWER cannot — without re-testing each endpoint's
own business logic (already covered in its own test file).
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import get_current_user
from app.db.database import get_db
from app.main import app
from app.models.alert import Alert
from app.models.detection_batch import DetectionBatch
from app.models.enums import AlertStatus, ProcessingStatus, Severity, UserRole


def fake_user(role: UserRole):
    class _FakeUser:
        id = uuid.uuid4()
        username = f"fake-{role.value}"
        role_value = role
        is_active = True

    _FakeUser.role = role
    return _FakeUser()


def client_as(db_session, role: UserRole):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: fake_user(role)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PENDING, total_records=0)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_alert(db_session, batch, **overrides) -> Alert:
    defaults = dict(
        batch_id=batch.id, row_number=1, title="t", description="d", severity=Severity.HIGH,
        status=AlertStatus.OPEN, evidence={},
    )
    alert = Alert(**{**defaults, **overrides})
    db_session.add(alert)
    db_session.commit()
    return alert


CSV_BYTES = b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n5,tcp,http,SF,100,200,normal\n"


class TestDetectionUploadRBAC:
    def test_viewer_cannot_upload_csv(self, db_session):
        client = client_as(db_session, UserRole.READ_ONLY_VIEWER)
        response = client.post("/api/v1/detection/upload", files={"file": ("x.csv", CSV_BYTES, "text/csv")})
        assert response.status_code == 403

    def test_analyst_can_upload_csv(self, db_session, tmp_path):
        from app.api.v1.detection import get_upload_config
        from app.services.upload_service import UploadConfig

        app.dependency_overrides[get_upload_config] = lambda: UploadConfig(directory=tmp_path, max_bytes=10_000_000)
        client = client_as(db_session, UserRole.SECURITY_ANALYST)
        response = client.post("/api/v1/detection/upload", files={"file": ("x.csv", CSV_BYTES, "text/csv")})
        assert response.status_code == 201

    def test_viewer_can_list_batches(self, db_session):
        add_batch(db_session)
        client = client_as(db_session, UserRole.READ_ONLY_VIEWER)
        response = client.get("/api/v1/detection/batches")
        assert response.status_code == 200

    def test_viewer_cannot_start_processing(self, db_session):
        batch = add_batch(db_session)
        client = client_as(db_session, UserRole.READ_ONLY_VIEWER)
        response = client.post(f"/api/v1/detection/batches/{batch.id}/process")
        assert response.status_code == 403


class TestAlertRBAC:
    def test_viewer_can_list_alerts(self, db_session):
        batch = add_batch(db_session)
        add_alert(db_session, batch)
        client = client_as(db_session, UserRole.READ_ONLY_VIEWER)
        response = client.get("/api/v1/alerts")
        assert response.status_code == 200

    def test_viewer_cannot_update_alert_status(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)
        client = client_as(db_session, UserRole.READ_ONLY_VIEWER)
        response = client.patch(f"/api/v1/alerts/{alert.id}/status", json={"status": "acknowledged"})
        assert response.status_code == 403

    def test_analyst_can_update_alert_status(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)
        client = client_as(db_session, UserRole.SECURITY_ANALYST)
        response = client.patch(f"/api/v1/alerts/{alert.id}/status", json={"status": "acknowledged"})
        assert response.status_code == 200

    def test_administrator_can_update_alert_status(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)
        client = client_as(db_session, UserRole.ADMINISTRATOR)
        response = client.patch(f"/api/v1/alerts/{alert.id}/status", json={"status": "acknowledged"})
        assert response.status_code == 200


class TestCaptureRBAC:
    def test_viewer_can_view_sessions_but_not_start_one(self, db_session):
        client = client_as(db_session, UserRole.READ_ONLY_VIEWER)
        list_response = client.get("/api/v1/capture/sessions")
        assert list_response.status_code == 200

        start_response = client.post("/api/v1/capture/sessions", json={"interface": "eth-rbac-test"})
        assert start_response.status_code == 403

    def test_analyst_can_attempt_to_start_a_session(self, db_session):
        # "Attempt" because this sandboxed test host has no capture
        # backend installed -- the point here is authorization, not
        # whether the capture itself ultimately succeeds (see
        # test_capture_api.py/test_live_capture.py for that).
        client = client_as(db_session, UserRole.SECURITY_ANALYST)
        response = client.post("/api/v1/capture/sessions", json={"interface": "eth-rbac-analyst"})
        assert response.status_code == 201

    def test_viewer_cannot_stop_a_session(self, db_session):
        analyst_client = client_as(db_session, UserRole.SECURITY_ANALYST)
        started = analyst_client.post("/api/v1/capture/sessions", json={"interface": "eth-rbac-stop"}).json()

        viewer_client = client_as(db_session, UserRole.READ_ONLY_VIEWER)
        response = viewer_client.post(f"/api/v1/capture/sessions/{started['id']}/stop")
        assert response.status_code == 403
