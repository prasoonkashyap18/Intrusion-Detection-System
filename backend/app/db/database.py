"""Database engine, session factory, and connectivity helpers.

This module is infrastructure only: it does not define any ORM models.
Future steps will add models under backend/app/models/ that inherit from
the declarative Base in base.py, and backend/app/db/init_db() will create
their tables.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base

logger = logging.getLogger("ai_ids")

# SQLite requires check_same_thread=False so the connection can be used
# across the worker threads FastAPI uses for sync route/dependency calls.
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=_connect_args)

if settings.database_url.startswith("sqlite"):
    # SQLite does not enforce foreign keys unless explicitly enabled per
    # connection; without this, batch_id/model_id FKs on DetectionResult
    # would be silently unenforced.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Session:
    """FastAPI dependency that yields a database session and always closes it.

    Usage (in a future route):
        def some_route(db: Session = Depends(get_db)): ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables registered on Base's metadata.

    Imports app.models so its ORM classes are registered on Base before
    create_all() runs. Does not insert any data.
    """
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def check_connection() -> bool:
    """Verify the configured database can be reached.

    Opens a session, executes a trivial query, and closes the session.
    Returns True on success, False if connectivity fails. Used internally
    for verification only — not exposed as a public API endpoint.
    """
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database connectivity check failed")
        return False
    finally:
        db.close()
