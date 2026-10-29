"""User provisioning and management (Step 46) — the only place `User`
rows are ever created or have their `role`/`is_active` changed. Used
by both `POST /api/v1/admin/users` (an existing `ADMINISTRATOR`,
enforced by `app.core.security.require_role` in the route) and
`backend/scripts/create_admin_user.py` (a one-time bootstrap run
directly on the server, never over HTTP).

There is deliberately no self-service path anywhere in this module or
its callers: nothing here lets a caller change *their own* role, and
no public registration endpoint exists at all — every write here is
an administrator acting on a (possibly different) user's row.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.enums import UserRole
from app.models.user import User
from app.services.password_hashing import hash_password
from app.services.session_service import revoke_all_sessions_for_user

logger = logging.getLogger("ai_ids")


def create_user(db: Session, *, username: str, password: str, role: UserRole) -> User:
    """Creates a new account. Raises `ApiException` 409 if `username`
    is already taken, 400 for an empty username/password (defense in
    depth — the API schema already validates this).
    """
    if not username or not username.strip():
        raise ApiException(400, "invalid_username", "Username must not be empty.")
    if not password:
        raise ApiException(400, "invalid_password", "Password must not be empty.")

    existing = db.scalars(select(User).where(User.username == username)).first()
    if existing is not None:
        raise ApiException(409, "username_taken", "That username is already in use.")

    user = User(id=uuid.uuid4(), username=username, password_hash=hash_password(password), role=role, is_active=True)
    try:
        db.add(user)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to create user")
        raise ApiException(500, "user_creation_failed", "Unable to create the user.") from None

    db.refresh(user)
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at.asc())).all())


def get_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ApiException(404, "user_not_found", "No user was found with that ID.")
    return user


def update_user(
    db: Session,
    user_id: uuid.UUID,
    *,
    role: UserRole | None,
    is_active: bool | None,
    acting_user_id: uuid.UUID,
) -> User:
    """Updates `role`/`is_active` for `user_id`. Raises `ApiException`
    409 if `acting_user_id == user_id` and the change would remove
    their own `ADMINISTRATOR` role or deactivate their own account —
    an administrator can still manage every *other* account (including
    demoting/disabling another administrator), but can never lock
    themselves out or strip their own elevated access through this
    endpoint. Revokes every existing session for the target user when
    their role changes or their account is deactivated, so an already-
    issued session cannot keep acting under the old permissions.
    """
    user = get_user_or_404(db, user_id)

    is_self = user.id == acting_user_id
    if is_self:
        if role is not None and role != UserRole.ADMINISTRATOR and user.role == UserRole.ADMINISTRATOR:
            raise ApiException(409, "cannot_demote_self", "You cannot remove your own administrator role.")
        if is_active is False:
            raise ApiException(409, "cannot_deactivate_self", "You cannot deactivate your own account.")

    role_changed = role is not None and role != user.role
    deactivated = is_active is False and user.is_active

    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to update user %s", user_id)
        raise ApiException(500, "user_update_failed", "Unable to update the user.") from None

    if role_changed or deactivated:
        revoke_all_sessions_for_user(db, user.id)

    db.refresh(user)
    return user


__all__ = ["create_user", "list_users", "get_user_or_404", "update_user"]
