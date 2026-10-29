"""Tests for the Step 46 admin user-management API:

    POST  /api/v1/admin/users
    GET   /api/v1/admin/users
    PATCH /api/v1/admin/users/{id}

All require UserRole.ADMINISTRATOR; a SECURITY_ANALYST or
READ_ONLY_VIEWER attempting any of them must be rejected with 403.
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


def fake_user(role: UserRole, user_id=None):
    class _FakeUser:
        id = user_id or uuid.uuid4()
        username = f"fake-{role.value}"
        is_active = True

    _FakeUser.role = role
    return _FakeUser()


@pytest.fixture()
def db_user(db_session):
    """A real, persisted administrator the admin-endpoint tests act
    *as* (needed for the self-demotion-prevention tests, which look up
    the acting user's id against a real row)."""
    user = User(
        id=uuid.uuid4(), username="real-admin", password_hash=hash_password("x"), role=UserRole.ADMINISTRATOR,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


def client_as(db_session, role_or_user):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: (
        role_or_user if not isinstance(role_or_user, UserRole) else fake_user(role_or_user)
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


class TestCreateUser:
    def test_administrator_can_create_a_user(self, db_session):
        client = client_as(db_session, UserRole.ADMINISTRATOR)
        response = client.post(
            "/api/v1/admin/users",
            json={"username": "newanalyst", "password": "a-strong-password", "role": "security_analyst"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["username"] == "newanalyst"
        assert body["role"] == "security_analyst"
        assert "password" not in body

    def test_security_analyst_cannot_create_a_user(self, db_session):
        client = client_as(db_session, UserRole.SECURITY_ANALYST)
        response = client.post(
            "/api/v1/admin/users", json={"username": "x", "password": "a-strong-password", "role": "read_only_viewer"}
        )
        assert response.status_code == 403

    def test_read_only_viewer_cannot_create_a_user(self, db_session):
        client = client_as(db_session, UserRole.READ_ONLY_VIEWER)
        response = client.post(
            "/api/v1/admin/users", json={"username": "y", "password": "a-strong-password", "role": "read_only_viewer"}
        )
        assert response.status_code == 403

    def test_duplicate_username_is_409(self, db_session):
        client = client_as(db_session, UserRole.ADMINISTRATOR)
        client.post("/api/v1/admin/users", json={"username": "dupe", "password": "a-strong-password", "role": "read_only_viewer"})
        response = client.post(
            "/api/v1/admin/users", json={"username": "dupe", "password": "a-strong-password", "role": "read_only_viewer"}
        )
        assert response.status_code == 409

    def test_short_password_is_422(self, db_session):
        client = client_as(db_session, UserRole.ADMINISTRATOR)
        response = client.post(
            "/api/v1/admin/users", json={"username": "shortpw", "password": "short", "role": "read_only_viewer"}
        )
        assert response.status_code == 422

    def test_invalid_role_value_is_422(self, db_session):
        client = client_as(db_session, UserRole.ADMINISTRATOR)
        response = client.post(
            "/api/v1/admin/users", json={"username": "badrole", "password": "a-strong-password", "role": "superuser"}
        )
        assert response.status_code == 422

    def test_creating_a_user_does_not_let_the_caller_choose_their_own_role_elevation(self, db_session):
        # The request body's "role" only ever applies to the brand-new
        # account being created -- there is no way to pass a role that
        # changes the caller's own.
        client = client_as(db_session, UserRole.ADMINISTRATOR)
        response = client.post(
            "/api/v1/admin/users", json={"username": "another-admin", "password": "a-strong-password", "role": "administrator"}
        )
        assert response.status_code == 201
        assert response.json()["role"] == "administrator"


class TestListUsers:
    def test_administrator_can_list_users(self, db_session):
        client = client_as(db_session, UserRole.ADMINISTRATOR)
        client.post("/api/v1/admin/users", json={"username": "listed", "password": "a-strong-password", "role": "read_only_viewer"})
        response = client.get("/api/v1/admin/users")
        assert response.status_code == 200
        assert any(item["username"] == "listed" for item in response.json()["items"])

    def test_analyst_cannot_list_users(self, db_session):
        client = client_as(db_session, UserRole.SECURITY_ANALYST)
        response = client.get("/api/v1/admin/users")
        assert response.status_code == 403

    def test_unauthenticated_request_is_401(self, db_session):
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides.pop(get_current_user, None)
        client = TestClient(app)
        response = client.get("/api/v1/admin/users")
        assert response.status_code == 401


class TestUpdateUser:
    def test_administrator_can_change_a_users_role(self, db_session, db_user):
        client = client_as(db_session, db_user)
        created = client.post(
            "/api/v1/admin/users", json={"username": "promote-me", "password": "a-strong-password", "role": "read_only_viewer"}
        ).json()

        response = client.patch(f"/api/v1/admin/users/{created['id']}", json={"role": "security_analyst"})
        assert response.status_code == 200
        assert response.json()["role"] == "security_analyst"

    def test_administrator_can_deactivate_another_user(self, db_session, db_user):
        client = client_as(db_session, db_user)
        created = client.post(
            "/api/v1/admin/users", json={"username": "deactivate-me", "password": "a-strong-password", "role": "read_only_viewer"}
        ).json()

        response = client.patch(f"/api/v1/admin/users/{created['id']}", json={"is_active": False})
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_analyst_cannot_update_users(self, db_session, db_user):
        analyst_client = client_as(db_session, UserRole.SECURITY_ANALYST)
        response = analyst_client.patch(f"/api/v1/admin/users/{db_user.id}", json={"role": "administrator"})
        assert response.status_code == 403

    def test_viewer_cannot_escalate_their_own_role_via_direct_api_request(self, db_session):
        viewer = fake_user(UserRole.READ_ONLY_VIEWER)
        client = client_as(db_session, viewer)
        # A viewer has no admin access at all -- attempting to PATCH
        # even their own (nonexistent, since it's a fake test identity)
        # user id is rejected before any role check on the target,
        # because require_role runs first.
        response = client.patch(f"/api/v1/admin/users/{viewer.id}", json={"role": "administrator"})
        assert response.status_code == 403

    def test_administrator_cannot_self_demote_via_the_api(self, db_session, db_user):
        client = client_as(db_session, db_user)
        response = client.patch(f"/api/v1/admin/users/{db_user.id}", json={"role": "read_only_viewer"})
        assert response.status_code == 409

    def test_administrator_cannot_self_deactivate_via_the_api(self, db_session, db_user):
        client = client_as(db_session, db_user)
        response = client.patch(f"/api/v1/admin/users/{db_user.id}", json={"is_active": False})
        assert response.status_code == 409

    def test_unknown_user_id_is_404(self, db_session, db_user):
        client = client_as(db_session, db_user)
        response = client.patch(f"/api/v1/admin/users/{uuid.uuid4()}", json={"role": "security_analyst"})
        assert response.status_code == 404
