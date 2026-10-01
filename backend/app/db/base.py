"""Declarative base for SQLAlchemy ORM models.

No application models exist yet. Future models (DetectionBatch,
DetectionResult, ModelMetadata, etc.) will inherit from `Base` in later
steps.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
