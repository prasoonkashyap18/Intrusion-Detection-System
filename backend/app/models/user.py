"""User: one application account (Step 46).

There is no public registration anywhere in this codebase — rows here
are created only by `backend/scripts/create_admin_user.py` (bootstrap,
run directly on the server, never over HTTP) or by an existing
`ADMINISTRATOR` through `POST /api/v1/admin/users` (see
`app.api.v1.admin`, which requires that role). Nothing in this
codebase ever lets a user set or change their own `role` — the only
place `role` is ever written after creation is that same admin-only
endpoint, acting on a *different* user's row.

`password_hash` is never the plaintext password and never a reversible
encoding of it — see `app.services.password_hashing` for the PBKDF2-
HMAC-SHA256 scheme used, with a fresh random salt per user.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.enums import UserRole


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    username: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SAEnum(
            UserRole,
            name="user_role",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=UserRole.READ_ONLY_VIEWER,
        index=True,
    )

    # A disabled account authenticates nowhere: app.services.auth_service
    # rejects a login attempt for an inactive user with the same generic
    # "invalid credentials" response an unknown username gets — never a
    # distinct "this account is disabled" message that would let a
    # caller enumerate valid usernames.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship("UserSession", back_populates="user")
