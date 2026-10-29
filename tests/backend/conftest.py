"""Makes the backend application package importable from the repo-level
tests/ directory, regardless of the working directory pytest is run from.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def db_session():
    """An isolated in-memory SQLite database, never the real dev database.

    Uses StaticPool so the same in-memory database is shared across
    connections within a single test, and enables SQLite foreign-key
    enforcement to match the production engine's configuration.
    """
    import app.models  # noqa: F401  (registers ORM models on Base)
    from app.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    session = session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _default_authenticated_user():
    """Every route this application exposes (besides `GET /health` and
    `POST /auth/login`) now requires authentication (Step 46) — see
    `app.core.security.get_current_user`. Every test written *before*
    that step exercises these endpoints with no login flow at all, so
    this autouse fixture supplies a default authenticated
    `ADMINISTRATOR` for the duration of each test by overriding the
    exact same FastAPI dependency a real request would resolve,
    exactly the way each test file already overrides `get_db`.

    This does not weaken authorization itself — the real dependency,
    and the real `require_role` checks inline in each route, are
    exactly what runs in production; only this test-only default
    *identity* is substituted. Tests that exercise authentication or
    authorization directly (missing/invalid/expired tokens, wrong-role
    access attempts) override this same dependency themselves with a
    different fake user, or clear it entirely, within their own test
    body/fixture — see `tests/backend/test_auth_api.py` and
    `tests/backend/test_rbac.py`.
    """
    import uuid
    from datetime import datetime, timezone

    from app.core.security import get_current_user
    from app.main import app
    from app.models.enums import UserRole

    class _DefaultTestUser:
        id = uuid.uuid4()
        username = "test-default-admin"
        role = UserRole.ADMINISTRATOR
        is_active = True
        created_at = datetime.now(timezone.utc)
        last_login_at = None

    app.dependency_overrides[get_current_user] = lambda: _DefaultTestUser()
    yield
    app.dependency_overrides.pop(get_current_user, None)
