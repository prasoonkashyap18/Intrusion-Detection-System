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
