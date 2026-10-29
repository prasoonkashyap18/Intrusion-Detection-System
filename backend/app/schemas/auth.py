"""API schemas for authentication and user management (Step 46).

Independent of `app.models.user.User` (the ORM model) by design — see
"Schema Separation" in backend/README.md. `UserResponse` deliberately
never includes `password_hash`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import UserRole


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None

    @field_validator("created_at")
    @classmethod
    def _assume_utc_created(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @field_validator("last_login_at")
    @classmethod
    def _assume_utc_last_login(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class UserListResponse(BaseModel):
    items: list[UserResponse]


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=256)
    role: UserRole


class UpdateUserRequest(BaseModel):
    """Every field optional — a caller sets only what it wants to
    change. Never accepts `username`: nothing in this API renames an
    existing account."""

    role: UserRole | None = None
    is_active: bool | None = None
