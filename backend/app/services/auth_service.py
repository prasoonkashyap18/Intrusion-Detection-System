"""Login/logout (Step 46) — the service layer `app.api.v1.auth` sits
on top of.

Login never reveals *why* an attempt failed: an unknown username, a
wrong password, and a disabled (`is_active=False`) account all produce
the exact same generic `401 invalid_credentials` — distinguishing them
in the response would let a caller enumerate valid usernames or probe
which accounts exist/are disabled. The real reason is logged server-
side only.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.user import User
from app.services.password_hashing import verify_password
from app.services.session_service import create_session

logger = logging.getLogger("ai_ids")

_INVALID_CREDENTIALS_MESSAGE = "Invalid username or password."


def login(db: Session, username: str, password: str, *, ttl_minutes: int) -> tuple[User, str]:
    """Verifies `username`/`password` and issues a new session. Raises
    `ApiException(401, "invalid_credentials", ...)` for any failure —
    unknown user, wrong password, or a disabled account (see the
    module docstring for why these are never distinguished in the
    response).
    """
    user = db.scalars(select(User).where(User.username == username)).first()

    if user is None:
        logger.info("Login attempt for unknown username")
        raise ApiException(401, "invalid_credentials", _INVALID_CREDENTIALS_MESSAGE)

    if not user.is_active:
        logger.info("Login attempt for disabled account %s", user.id)
        raise ApiException(401, "invalid_credentials", _INVALID_CREDENTIALS_MESSAGE)

    if not verify_password(password, user.password_hash):
        logger.info("Login attempt with wrong password for user %s", user.id)
        raise ApiException(401, "invalid_credentials", _INVALID_CREDENTIALS_MESSAGE)

    from datetime import datetime, timezone

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    raw_token, _session = create_session(db, user, ttl_minutes=ttl_minutes)
    return user, raw_token


__all__ = ["login"]
