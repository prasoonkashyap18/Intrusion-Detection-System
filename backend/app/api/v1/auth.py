"""Authentication endpoints (Step 46): login, logout, and "who am I".

No registration endpoint exists here or anywhere in this codebase —
see `app.api.v1.admin` for the only (administrator-only) way a new
account is created over HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.core.config import settings
from app.core.errors import ApiException
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserResponse
from app.schemas.common import ErrorResponse
from app.services import auth_service
from app.services.session_service import get_active_session, revoke_session
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth")


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate with a username and password",
    description=(
        "The one deliberate public endpoint besides GET /health — a caller cannot authenticate "
        "without it. Returns a bearer token to send as `Authorization: Bearer <token>` on every "
        "subsequent request. An unknown username, a wrong password, and a disabled account all "
        "return the same generic 401 — never a distinct message that would let a caller "
        "enumerate valid usernames."
    ),
    responses={401: {"model": ErrorResponse, "description": "Invalid username or password"}},
)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user, token = auth_service.login(db, body.username, body.password, ttl_minutes=settings.session_ttl_minutes)
    return LoginResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post(
    "/logout",
    status_code=204,
    summary="Revoke the current session",
    description="Immediately revokes the session the Authorization header identifies.",
    responses={401: {"model": ErrorResponse, "description": "Not authenticated"}},
)
def logout(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> None:
    token = authorization[len("Bearer "):].strip() if authorization else None
    if token:
        session = get_active_session(db, token)
        if session is not None:
            revoke_session(db, session)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Retrieve the authenticated user's own profile",
    responses={401: {"model": ErrorResponse, "description": "Not authenticated"}},
)
def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)
