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


class AlertStatus(str, enum.Enum):
    """Lifecycle state of a persisted `Alert` (Step 37). Centralized
    valid-transition logic lives in `app.services.alert_lifecycle`, not
    here or on the model itself — this enum only defines the controlled
    value set."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class CorrelationStrength(str, enum.Enum):
    """How strong the evidence behind one `EventCorrelation` row is
    (Step 38) — never a confidence score, just a documented two-level
    classification of which rule matched."""

    STRONG = "strong"
    """Based on an exact match of a real, persisted network attribute
    (e.g. the same source or destination IP) within the configured time
    window — not a guess."""

    WEAK = "weak"
    """A heuristic association (e.g. row-number proximity within the
    same batch) with no matching network attribute — recorded as
    weaker evidence, never presented as equivalent to a STRONG match."""


class ChainClassification(str, enum.Enum):
    """How an `AttackChain` (Step 39) is classified, based strictly on
    the correlation evidence connecting its member alerts — never a
    claim of a confirmed campaign or a MITRE ATT&CK mapping."""

    CORROBORATED = "corroborated"
    """At least one STRONG correlation edge connects members of this
    chain."""

    HEURISTIC = "heuristic"
    """Every correlation edge connecting this chain's members is WEAK —
    an observed grouping, not evidence of a real relationship."""


class CaptureSessionStatus(str, enum.Enum):
    """Lifecycle state of a `CaptureSession` (Step 42). Centralized
    transition handling lives in `app.services.live_capture`, mirroring
    `AlertStatus`'s own convention."""

    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class ModelStatus(str, enum.Enum):
    """A `ModelMetadata` row is only ever created *after* its artifact has
    been written successfully (see app.services.model_training), so
    `READY` is the only status this MVP ever persists. `FAILED` exists for
    forward compatibility with a future step that may want to record a
    training attempt that did not produce a usable model, rather than
    simply not writing a row at all, as this step does."""

    READY = "ready"
    FAILED = "failed"
