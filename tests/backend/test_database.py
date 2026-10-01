"""Tests for the database infrastructure (engine, session, connectivity).

These tests use an isolated in-memory SQLite database exclusively. They
must never touch, create, or depend on the real development database file
at backend/data/ai_ids.db.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _in_memory_engine():
    return create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


def test_engine_session_and_basic_query_lifecycle():
    engine = _in_memory_engine()
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    session = SessionLocal()
    try:
        result = session.execute(text("SELECT 1"))
        assert result.scalar() == 1
    finally:
        session.close()

    engine.dispose()


def test_declarative_base_create_all_is_safe_with_no_models_registered():
    from app.db.base import Base

    engine = _in_memory_engine()
    # No application models exist yet; this must not raise.
    Base.metadata.create_all(bind=engine)
    engine.dispose()


def test_check_connection_against_isolated_in_memory_database(monkeypatch):
    import app.db.database as database_module

    test_engine = _in_memory_engine()
    test_session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)

    monkeypatch.setattr(database_module, "engine", test_engine)
    monkeypatch.setattr(database_module, "SessionLocal", test_session_local)

    assert database_module.check_connection() is True

    test_engine.dispose()
