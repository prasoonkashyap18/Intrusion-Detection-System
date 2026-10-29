"""Controlled value sets exposed at the API boundary.

Defined independently of app.models.enums (which backs the database) so
that API contracts do not import or couple to the ORM layer — see
"Schema Separation" in backend/README.md. Values are kept consistent with
the database's controlled sets by convention.
"""

from __future__ import annotations

import enum


class ProcessingStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class CaptureSessionStatus(str, enum.Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
