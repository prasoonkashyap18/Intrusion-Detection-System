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
