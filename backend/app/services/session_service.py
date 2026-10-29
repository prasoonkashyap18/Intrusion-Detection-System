"""Session issuance and revocation (Step 46): opaque, high-entropy
bearer tokens, hashed before storage (`app.models.user_session.
UserSession.token_hash`) — the raw token the client holds is never
persisted anywhere, only its SHA-256 digest, so a database compromise
alone can never be replayed as a valid session.

Tokens are generated with `secrets.token_urlsafe` (256 bits of entropy
internally — far more than any password), so hashing them before
lookup needs no slow KDF/salt the way a password does: the token
itself already has enough entropy that a straight SHA-256 digest is
an acceptable, fast lookup key (this is the same reasoning most API-key
storage schemes use).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.user_session import UserSession

_TOKEN_BYTES = 32


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User, *, ttl_minutes: int) -> tuple[str, UserSession]:
    """Issues a new session for `user`. Returns `(raw_token, UserSession)`
    — `raw_token` is the only time the caller (ultimately, the login
    response) ever sees the literal value; only its hash is persisted.
    """
    raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
    now = datetime.now(timezone.utc)
    session = UserSession(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return raw_token, session


def get_active_session(db: Session, raw_token: str) -> UserSession | None:
    """Returns the `UserSession` for `raw_token` if it exists, has not
    been revoked, and has not expired — `None` for every other case
    (unknown token, revoked, expired), deliberately without
    distinguishing which, so an invalid-token response never leaks
    which reason applied."""
    token_hash = _hash_token(raw_token)
    session = db.scalars(select(UserSession).where(UserSession.token_hash == token_hash)).first()
    if session is None:
        return None
    now = datetime.now(timezone.utc)
    expires_at = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
    if session.revoked_at is not None or expires_at < now:
        return None
    return session


def revoke_session(db: Session, session: UserSession) -> None:
    """Logout: marks the session revoked immediately — a replayed copy
    of this exact token is rejected by `get_active_session` from this
    moment on, regardless of its remaining nominal lifetime."""
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()


def revoke_all_sessions_for_user(db: Session, user_id: uuid.UUID) -> None:
    """Revokes every active session for `user_id` — used when an
    administrator disables an account, so existing sessions cannot keep
    working after that decision."""
    now = datetime.now(timezone.utc)
    sessions = db.scalars(
        select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    ).all()
    for session in sessions:
        session.revoked_at = now
    db.commit()


__all__ = ["create_session", "get_active_session", "revoke_session", "revoke_all_sessions_for_user"]
