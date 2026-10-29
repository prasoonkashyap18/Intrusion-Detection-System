"""Tests for the Step 46 auth API:

    POST /api/v1/auth/login
    POST /api/v1/auth/logout
    GET  /api/v1/auth/me

These tests deliberately clear the autouse `_default_authenticated_user`
override (see conftest.py) wherever they need to exercise *real*
authentication rather than the test-default identity.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import get_current_user
from app.db.database import get_db
from app.main import app
from app.models.enums import UserRole
from app.models.user import User
from app.services.password_hashing import hash_password


@pytest.fixture()
def real_auth_client(db_session):
    """A client where `get_current_user` is NOT overridden -- requests
    go through the real dependency, so login/logout/protected-route
    behavior is tested for real, not against the autouse test default."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides.pop(get_current_user, None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_user(db_session, *, username="alice", password="correct-password", role=UserRole.SECURITY_ANALYST, is_active=True) -> User:
    user = User(id=uuid.uuid4(), username=username, password_hash=hash_password(password), role=role, is_active=is_active)
    db_session.add(user)
    db_session.commit()
    return user


class TestLoginEndpoint:
    def test_correct_credentials_return_a_token(self, real_auth_client, db_session):
        add_user(db_session, username="alice", password="correct-password")
        response = real_auth_client.post("/api/v1/auth/login", json={"username": "alice", "password": "correct-password"})
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert len(body["access_token"]) > 20
        assert body["user"]["username"] == "alice"
        assert "password" not in body["user"]
        assert "password_hash" not in body["user"]

    def test_wrong_password_is_401(self, real_auth_client, db_session):
        add_user(db_session, username="alice", password="correct-password")
        response = real_auth_client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"})
        assert response.status_code == 401

    def test_disabled_account_is_401(self, real_auth_client, db_session):
        add_user(db_session, username="disabled-user", password="correct-password", is_active=False)
        response = real_auth_client.post(
            "/api/v1/auth/login", json={"username": "disabled-user", "password": "correct-password"}
        )
        assert response.status_code == 401

    def test_unknown_username_is_401(self, real_auth_client):
        response = real_auth_client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"})
        assert response.status_code == 401

    def test_missing_fields_is_422(self, real_auth_client):
        response = real_auth_client.post("/api/v1/auth/login", json={})
        assert response.status_code == 422

    def test_token_actually_authenticates_a_protected_endpoint(self, real_auth_client, db_session):
        add_user(db_session, username="alice", password="correct-password")
        login_response = real_auth_client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "correct-password"}
        )
        token = login_response.json()["access_token"]

        me_response = real_auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_response.status_code == 200
        assert me_response.json()["username"] == "alice"


class TestUnauthenticatedAccess:
    def test_me_without_a_token_is_401(self, real_auth_client):
        response = real_auth_client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_me_with_a_malformed_header_is_401(self, real_auth_client):
        response = real_auth_client.get("/api/v1/auth/me", headers={"Authorization": "NotBearer abc"})
        assert response.status_code == 401

    def test_me_with_an_unknown_token_is_401(self, real_auth_client):
        response = real_auth_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
        assert response.status_code == 401

    def test_protected_detection_endpoint_without_a_token_is_401(self, real_auth_client):
        response = real_auth_client.get("/api/v1/detection/batches")
        assert response.status_code == 401

    def test_protected_alerts_endpoint_without_a_token_is_401(self, real_auth_client):
        response = real_auth_client.get("/api/v1/alerts")
        assert response.status_code == 401

    def test_protected_capture_endpoint_without_a_token_is_401(self, real_auth_client):
        response = real_auth_client.get("/api/v1/capture/sessions")
        assert response.status_code == 401

    def test_health_remains_public(self, real_auth_client):
        response = real_auth_client.get("/api/v1/health")
        assert response.status_code == 200


class TestLogout:
    def test_logout_revokes_the_session(self, real_auth_client, db_session):
        add_user(db_session, username="alice", password="correct-password")
        token = real_auth_client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "correct-password"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        logout_response = real_auth_client.post("/api/v1/auth/logout", headers=headers)
        assert logout_response.status_code == 204

        me_response = real_auth_client.get("/api/v1/auth/me", headers=headers)
        assert me_response.status_code == 401

    def test_logout_without_a_token_is_401(self, real_auth_client):
        response = real_auth_client.post("/api/v1/auth/logout")
        assert response.status_code == 401
