"""FastAPI authentication/authorization primitives (Step 46).

`get_current_user` is the single dependency every protected route in
this application uses — see `app/api/v1/*.py`. It is also the single
seam tests override (`app.main.app.dependency_overrides[get_current_user]
= lambda: <fake user>`) to supply an authenticated context without a
real login flow; `tests/backend/conftest.py` does this by default for
every test (an `ADMINISTRATOR`, so every pre-existing test — written
before this step introduced authorization — keeps passing unchanged),
while this step's own authorization tests override it with other
roles, or remove the override entirely, to exercise 401/403 paths for
real.

`require_role` is a plain Python helper (not a second `Depends`), used
inline inside a route after `current_user: User = Depends(get_current_user)`
— so authorization has exactly one override seam (`get_current_user`)
to manage in tests, never a second one per role combination.
"""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.db.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.services.session_service import get_active_session

_BEARER_PREFIX = "Bearer "


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise ApiException(401, "not_authenticated", "A valid Authorization: Bearer token is required.")
    token = authorization[len(_BEARER_PREFIX):].strip()
    if not token:
        raise ApiException(401, "not_authenticated", "A valid Authorization: Bearer token is required.")
    return token


def authenticate_token(db: Session, token: str) -> User:
    """Resolves a raw bearer token to its `User`, regardless of where
    the caller got the token from (the `Authorization` header for
    every ordinary request, or a query parameter for `GET /alerts/
    stream` — see that route's own docstring for why Server-Sent
    Events needs that exception: browsers' `EventSource` cannot set
    custom request headers). Raises `ApiException` 401 for an unknown,
    expired, or revoked session token, or a session whose owning
    account has since been disabled — never a distinct message per
    case, for the same reason `app.services.auth_service.login` never
    distinguishes its own failure reasons.
    """
    session = get_active_session(db, token)
    if session is None:
        raise ApiException(401, "not_authenticated", "The session is missing, expired, or has been revoked.")

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise ApiException(401, "not_authenticated", "The session is missing, expired, or has been revoked.")

    return user


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolves the bearer token in `Authorization` to a `User` — the
    dependency every ordinary (non-SSE) protected route uses."""
    token = _extract_bearer_token(authorization)
    return authenticate_token(db, token)


def require_role(current_user: User, *allowed_roles: UserRole) -> None:
    """Raises `ApiException` 403 if `current_user.role` is not one of
    `allowed_roles`. Called inline inside a route body, after `Depends
    (get_current_user)` has already established *who* the caller is —
    this function only ever answers *whether they may do this*."""
    if current_user.role not in allowed_roles:
        raise ApiException(
            403, "insufficient_permissions", "Your role does not have permission to perform this action."
        )


__all__ = ["get_current_user", "authenticate_token", "require_role"]
