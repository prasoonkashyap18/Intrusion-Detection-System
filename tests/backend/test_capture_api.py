"""Tests for the Step 42 capture API:

    GET  /api/v1/capture/interfaces
    POST /api/v1/capture/sessions
    GET  /api/v1/capture/sessions
    GET  /api/v1/capture/sessions/{id}
    POST /api/v1/capture/sessions/{id}/stop

Exercised against the real service layer (no privileged interface
required): this sandboxed CI host has no Npcap/WinPcap installed, so a
started session's capture thread genuinely fails quickly and
deterministically — exactly the "missing capture backend produces a
clear failure" behavior Step 42 requires, demonstrated for real rather
than mocked. Nothing here depends on root/administrator privileges or
an actual network interface being capturable.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_db
from app.main import app


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def wait_for_status(client, session_id, statuses, timeout=10.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/v1/capture/sessions/{session_id}")
        last = response.json()["status"]
        if last in statuses:
            return last
        time.sleep(0.1)
    return last


class TestInterfaceDiscoveryEndpoint:
    def test_returns_a_list(self, client):
        response = client.get("/api/v1/capture/interfaces")
        assert response.status_code == 200
        assert isinstance(response.json()["interfaces"], list)


class TestStartCaptureEndpoint:
    def test_missing_interface_is_422(self, client):
        response = client.post("/api/v1/capture/sessions", json={"interface": ""})
        assert response.status_code == 422

    def test_missing_body_field_is_422(self, client):
        response = client.post("/api/v1/capture/sessions", json={})
        assert response.status_code == 422

    def test_start_returns_201_with_a_session(self, client):
        response = client.post("/api/v1/capture/sessions", json={"interface": "api-test-iface-1"})
        assert response.status_code == 201
        body = response.json()
        assert body["interface_name"] == "api-test-iface-1"
        assert body["status"] in ("running", "failed")

    def test_capture_backend_unavailable_eventually_reports_failed(self, client):
        # This host has no Npcap/WinPcap -- the capture thread must
        # discover that and mark the session FAILED, with a clear,
        # non-empty error message, never leaving it RUNNING forever.
        response = client.post("/api/v1/capture/sessions", json={"interface": "api-test-iface-2"})
        session_id = response.json()["id"]

        final_status = wait_for_status(client, session_id, {"failed", "stopped"})
        assert final_status == "failed"
        detail = client.get(f"/api/v1/capture/sessions/{session_id}").json()
        assert detail["error_message"]


class TestListAndGetSessionEndpoints:
    def test_list_includes_a_started_session(self, client):
        started = client.post("/api/v1/capture/sessions", json={"interface": "api-test-iface-3"}).json()
        response = client.get("/api/v1/capture/sessions")
        assert response.status_code == 200
        assert any(item["id"] == started["id"] for item in response.json()["items"])

    def test_get_unknown_session_is_404(self, client):
        response = client.get(f"/api/v1/capture/sessions/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_get_malformed_session_id_is_422(self, client):
        response = client.get("/api/v1/capture/sessions/not-a-uuid")
        assert response.status_code == 422


class TestStopCaptureEndpoint:
    def test_stop_unknown_session_is_404(self, client):
        response = client.post(f"/api/v1/capture/sessions/{uuid.uuid4()}/stop")
        assert response.status_code == 404

    def test_stopping_a_failed_session_is_409(self, client):
        started = client.post("/api/v1/capture/sessions", json={"interface": "api-test-iface-4"}).json()
        wait_for_status(client, started["id"], {"failed", "stopped"})

        response = client.post(f"/api/v1/capture/sessions/{started['id']}/stop")
        assert response.status_code == 409
