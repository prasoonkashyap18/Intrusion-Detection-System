"""User-management endpoints (Step 46) — the only way a new account is
ever created or an existing one's role/active status is ever changed
over HTTP, and every one of them requires `UserRole.ADMINISTRATOR`.
There is no public registration endpoint, and no endpoint anywhere in
this codebase lets a caller change their own role.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_role
from app.db.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.auth import CreateUserRequest, UpdateUserRequest, UserListResponse, UserResponse
from app.schemas.common import ErrorResponse
from app.services import user_management_service

router = APIRouter(prefix="/admin/users")


@router.post(
    "",
    response_model=UserResponse,
    status_code=201,
    summary="Create a new user account (administrator only)",
    description="The only way a new account is created over HTTP — there is no public registration.",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Caller is not an administrator"},
        409: {"model": ErrorResponse, "description": "Username already taken"},
    },
)
def create_user(
    body: CreateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    require_role(current_user, UserRole.ADMINISTRATOR)
    user = user_management_service.create_user(db, username=body.username, password=body.password, role=body.role)
    return UserResponse.model_validate(user)


@router.get(
    "",
    response_model=UserListResponse,
    summary="List every user account (administrator only)",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Caller is not an administrator"},
    },
)
def get_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserListResponse:
    require_role(current_user, UserRole.ADMINISTRATOR)
    return UserListResponse(items=[UserResponse.model_validate(user) for user in user_management_service.list_users(db)])


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update a user's role or active status (administrator only)",
    description=(
        "An administrator may change any other account's role or active status, but may never "
        "remove their own administrator role or deactivate their own account through this "
        "endpoint — see app.services.user_management_service.update_user."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Caller is not an administrator"},
        404: {"model": ErrorResponse, "description": "No user exists with that ID"},
        409: {"model": ErrorResponse, "description": "The caller attempted to self-demote or self-deactivate"},
    },
)
def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    require_role(current_user, UserRole.ADMINISTRATOR)
    user = user_management_service.update_user(
        db, user_id, role=body.role, is_active=body.is_active, acting_user_id=current_user.id
    )
    return UserResponse.model_validate(user)
