"""Controlled value sets shared by the MVP ORM models.

Using str Enums here means SQLAlchemy's Enum type stores the literal
string value and backends (SQLite via CHECK constraint, PostgreSQL via
native enum type) both enforce the allowed set.
"""

from __future__ import annotations

import enum


class ProcessingStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(str, enum.Enum):
    """MVP severity derived from a transparent rule-based mapping of
    prediction + confidence. Not output by a trained risk model."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ModelStatus(str, enum.Enum):
    """A `ModelMetadata` row is only ever created *after* its artifact has
    been written successfully (see app.services.model_training), so
    `READY` is the only status this MVP ever persists. `FAILED` exists for
    forward compatibility with a future step that may want to record a
    training attempt that did not produce a usable model, rather than
    simply not writing a row at all, as this step does."""

    READY = "ready"
    FAILED = "failed"
