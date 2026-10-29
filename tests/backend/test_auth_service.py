"""Tests for app.services.auth_service/session_service/
user_management_service: login success/failure, disabled accounts,
session issuance, expiry, and revocation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import ApiException
from app.models.enums import UserRole
from app.models.user import User
from app.services import auth_service
from app.services.password_hashing import hash_password
from app.services.session_service import create_session, get_active_session, revoke_all_sessions_for_user, revoke_session
from app.services.user_management_service import create_user, get_user_or_404, update_user


def add_user(db_session, *, username="alice", password="correct-password", role=UserRole.SECURITY_ANALYST, is_active=True) -> User:
    user = User(id=uuid.uuid4(), username=username, password_hash=hash_password(password), role=role, is_active=is_active)
    db_session.add(user)
    db_session.commit()
    return user


class TestLogin:
    def test_correct_credentials_succeed(self, db_session):
        add_user(db_session, username="alice", password="correct-password")
        user, token = auth_service.login(db_session, "alice", "correct-password", ttl_minutes=60)
        assert user.username == "alice"
        assert isinstance(token, str) and len(token) > 20

    def test_wrong_password_fails_with_401(self, db_session):
        add_user(db_session, username="alice", password="correct-password")
        with pytest.raises(ApiException) as exc_info:
            auth_service.login(db_session, "alice", "wrong-password", ttl_minutes=60)
        assert exc_info.value.status_code == 401

    def test_unknown_username_fails_with_401(self, db_session):
        with pytest.raises(ApiException) as exc_info:
            auth_service.login(db_session, "nobody", "whatever", ttl_minutes=60)
        assert exc_info.value.status_code == 401

    def test_disabled_account_fails_with_the_same_401(self, db_session):
        add_user(db_session, username="bob", password="correct-password", is_active=False)
        with pytest.raises(ApiException) as exc_info:
            auth_service.login(db_session, "bob", "correct-password", ttl_minutes=60)
        assert exc_info.value.status_code == 401

    def test_unknown_user_and_wrong_password_produce_identical_messages(self, db_session):
        add_user(db_session, username="alice", password="correct-password")
        try:
            auth_service.login(db_session, "alice", "wrong-password", ttl_minutes=60)
        except ApiException as wrong_password_error:
            message_a = wrong_password_error.message
        try:
            auth_service.login(db_session, "no-such-user", "whatever", ttl_minutes=60)
        except ApiException as unknown_user_error:
            message_b = unknown_user_error.message
        assert message_a == message_b  # never distinguishable

    def test_successful_login_records_last_login_at(self, db_session):
        user = add_user(db_session, username="alice", password="correct-password")
        assert user.last_login_at is None
        auth_service.login(db_session, "alice", "correct-password", ttl_minutes=60)
        db_session.refresh(user)
        assert user.last_login_at is not None

    def test_a_disabled_account_cannot_use_a_session_it_was_issued_before_being_disabled(self, db_session):
        user = add_user(db_session, username="carol", password="correct-password")
        _, token = auth_service.login(db_session, "carol", "correct-password", ttl_minutes=60)
        user.is_active = False
        db_session.commit()

        session = get_active_session(db_session, token)
        assert session is not None  # the session row itself is still "active"
        # but app.core.security.get_current_user additionally checks
        # user.is_active before accepting it -- covered in test_auth_api.py.


class TestSessionLifecycle:
    def test_create_session_returns_a_usable_token(self, db_session):
        user = add_user(db_session)
        token, session = create_session(db_session, user, ttl_minutes=60)
        found = get_active_session(db_session, token)
        assert found is not None
        assert found.id == session.id

    def test_the_raw_token_is_never_stored(self, db_session):
        user = add_user(db_session)
        token, session = create_session(db_session, user, ttl_minutes=60)
        assert session.token_hash != token

    def test_an_unknown_token_is_not_active(self, db_session):
        assert get_active_session(db_session, "not-a-real-token") is None

    def test_an_expired_session_is_not_active(self, db_session):
        user = add_user(db_session)
        token, session = create_session(db_session, user, ttl_minutes=60)
        session.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        assert get_active_session(db_session, token) is None

    def test_a_revoked_session_is_not_active(self, db_session):
        user = add_user(db_session)
        token, session = create_session(db_session, user, ttl_minutes=60)
        revoke_session(db_session, session)
        assert get_active_session(db_session, token) is None

    def test_revoke_all_sessions_for_user_revokes_every_one(self, db_session):
        user = add_user(db_session)
        token_a, _ = create_session(db_session, user, ttl_minutes=60)
        token_b, _ = create_session(db_session, user, ttl_minutes=60)

        revoke_all_sessions_for_user(db_session, user.id)

        assert get_active_session(db_session, token_a) is None
        assert get_active_session(db_session, token_b) is None

    def test_revoking_one_users_sessions_does_not_affect_another_users(self, db_session):
        user_a = add_user(db_session, username="alice")
        user_b = add_user(db_session, username="bob")
        token_a, _ = create_session(db_session, user_a, ttl_minutes=60)
        token_b, _ = create_session(db_session, user_b, ttl_minutes=60)

        revoke_all_sessions_for_user(db_session, user_a.id)

        assert get_active_session(db_session, token_a) is None
        assert get_active_session(db_session, token_b) is not None


class TestUserManagementService:
    def test_create_user_rejects_duplicate_username(self, db_session):
        create_user(db_session, username="dupe", password="password123", role=UserRole.READ_ONLY_VIEWER)
        with pytest.raises(ApiException) as exc_info:
            create_user(db_session, username="dupe", password="password123", role=UserRole.READ_ONLY_VIEWER)
        assert exc_info.value.status_code == 409

    def test_create_user_rejects_empty_username(self, db_session):
        with pytest.raises(ApiException) as exc_info:
            create_user(db_session, username="", password="password123", role=UserRole.READ_ONLY_VIEWER)
        assert exc_info.value.status_code == 400

    def test_update_user_changes_role_and_revokes_sessions(self, db_session):
        admin = add_user(db_session, username="admin", role=UserRole.ADMINISTRATOR)
        target = add_user(db_session, username="target", role=UserRole.READ_ONLY_VIEWER)
        token, _ = create_session(db_session, target, ttl_minutes=60)

        update_user(db_session, target.id, role=UserRole.SECURITY_ANALYST, is_active=None, acting_user_id=admin.id)

        db_session.refresh(target)
        assert target.role == UserRole.SECURITY_ANALYST
        assert get_active_session(db_session, token) is None  # old session revoked

    def test_update_user_deactivating_revokes_sessions(self, db_session):
        admin = add_user(db_session, username="admin2", role=UserRole.ADMINISTRATOR)
        target = add_user(db_session, username="target2", role=UserRole.SECURITY_ANALYST)
        token, _ = create_session(db_session, target, ttl_minutes=60)

        update_user(db_session, target.id, role=None, is_active=False, acting_user_id=admin.id)

        assert get_active_session(db_session, token) is None

    def test_admin_cannot_self_demote(self, db_session):
        admin = add_user(db_session, username="self-admin", role=UserRole.ADMINISTRATOR)
        with pytest.raises(ApiException) as exc_info:
            update_user(db_session, admin.id, role=UserRole.READ_ONLY_VIEWER, is_active=None, acting_user_id=admin.id)
        assert exc_info.value.status_code == 409

    def test_admin_cannot_self_deactivate(self, db_session):
        admin = add_user(db_session, username="self-admin-2", role=UserRole.ADMINISTRATOR)
        with pytest.raises(ApiException) as exc_info:
            update_user(db_session, admin.id, role=None, is_active=False, acting_user_id=admin.id)
        assert exc_info.value.status_code == 409

    def test_admin_can_demote_a_different_admin(self, db_session):
        admin = add_user(db_session, username="acting-admin", role=UserRole.ADMINISTRATOR)
        other_admin = add_user(db_session, username="other-admin", role=UserRole.ADMINISTRATOR)

        updated = update_user(db_session, other_admin.id, role=UserRole.READ_ONLY_VIEWER, is_active=None, acting_user_id=admin.id)

        assert updated.role == UserRole.READ_ONLY_VIEWER

    def test_get_user_or_404_raises_for_unknown_id(self, db_session):
        with pytest.raises(ApiException) as exc_info:
            get_user_or_404(db_session, uuid.uuid4())
        assert exc_info.value.status_code == 404
