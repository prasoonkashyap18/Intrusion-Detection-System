"""Database engine, session factory, and connectivity helpers.

This module is infrastructure only: it does not define any ORM models.
Future steps will add models under backend/app/models/ that inherit from
the declarative Base in base.py, and backend/app/db/init_db() will create
their tables.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
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

    Safe to call with zero models registered — it simply creates no
    tables yet. Intended to be called once real models exist.
    """
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
