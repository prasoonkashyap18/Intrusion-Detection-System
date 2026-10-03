"""Typed representations for a dataset profile (Step 19).

All frozen dataclasses, independent of the SQLAlchemy ORM — a profile is a
computed, in-memory artifact, never persisted (see "Why profiles are not
persisted" in backend/README.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SchemaDetectionStatus(str, Enum):
    """How confidently `app.services.dataset_adapters` identified this
    dataset's schema. A profile never treats automatic recognition as
    certain — this status, and `DatasetIdentity.candidate_schema_ids`,
    keep "recognized" distinct from "unsupported" and "ambiguous" rather
    than collapsing all three into a single guess."""

    RECOGNIZED = "recognized"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class DatasetIdentity:
    status: SchemaDetectionStatus
    schema_id: str | None
    """The matched adapter's `schema_id`, only when `status` is
    `RECOGNIZED`. `None` for `UNSUPPORTED`/`AMBIGUOUS` — never a guess."""
    schema_description: str | None
    candidate_schema_ids: tuple[str, ...]
    """Every adapter whose `can_handle` matched this header. Length 0 for
    `UNSUPPORTED`, length > 1 for `AMBIGUOUS`, length 1 for `RECOGNIZED`."""


@dataclass(frozen=True)
class RowSummary:
    total_rows: int
    """Data rows actually counted (the header is not counted)."""
    rows_inspected: int
    """How many rows this profile's per-row statistics were computed
    over. Equal to `total_rows` under this profiler's design: a profile is
    only ever produced after the whole file has been read (a structural
    read failure raises instead of yielding a partial profile — see
    `profiler.profile_dataset`), so there is no scenario today where this
    differs from `total_rows`. Kept as its own field rather than assumed
    equal, so a future change that *does* introduce early stopping cannot
    silently start claiming a partial count is the total."""
    is_exact: bool
    """True when `total_rows` reflects every data row in the file. Always
    `True` for any `DatasetProfile` this module actually returns, for the
    same reason as `rows_inspected` above — an explicit field rather than
    a hardcoded assumption elsewhere, so this stays honest if that ever
    changes."""


class InferredType(str, Enum):
    """A column's apparent data type, inferred conservatively from its
    text. Advisory only: inference never coerces, overwrites, or discards
    any value anywhere else in the pipeline — ingestion, feature
    extraction and the dataset-adapter layer all keep every value's raw
    text exactly as the CSV wrote it, regardless of what is inferred here.
    """

    EMPTY = "empty"
    """Every value in the column was missing (or the column had zero
    rows to observe)."""
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    IP_ADDRESS = "ip_address"
    TIMESTAMP = "timestamp"
    TEXT = "text"
    MIXED = "mixed"
    """More than one of the categories above was observed among the
    column's non-missing values. Reported honestly rather than collapsed
    into whichever type happened to be most common."""


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    """The original header text, verbatim."""
    canonical_name: str | None
    """The canonical feature name this column was mapped to by the
    recognized dataset adapter, if any — `None` when unrecognized or when
    no adapter matched this dataset at all."""
    inferred_type: InferredType
    non_null_count: int
    missing_count: int
    missing_percentage: float | None
    """`missing_count / (non_null_count + missing_count) * 100`, or `None`
    when the dataset has zero rows (avoiding division by zero rather than
    reporting a misleading 0% or 100%)."""
    unique_count: int
    """Distinct non-missing values observed, up to
    `profiler.MAX_TRACKED_UNIQUE_VALUES`. See `unique_count_is_exact`."""
    unique_count_is_exact: bool
    """`False` once a column's distinct-value tracking hit
    `profiler.MAX_TRACKED_UNIQUE_VALUES` — at that point `unique_count` is
    a lower bound ("at least this many distinct values"), not the true
    count, and is never presented as exact."""
    is_recognized: bool
    is_label: bool
    is_attack_category: bool
    minimum: float | None = None
    """The smallest value seen that parses as a finite number, or `None`
    if the column has no such value. Independent of `inferred_type` — a
    column classified `MIXED` can still report a minimum/maximum over
    whichever of its values happened to be numeric."""
    maximum: float | None = None


@dataclass(frozen=True)
class LabelSummary:
    """Shared shape for both label and attack-category profiling — see
    `DatasetProfile.label_summary` / `.attack_category_summary`."""

    available: bool
    """`False` when no column was recognized for this concept. The
    single source of truth for "no label exists" — never inferred or
    assumed `True` from the presence of any other field."""
    column_name: str | None
    """The original source column name, when `available`."""
    value_counts: dict[str, int]
    """Observed value -> exact count, up to `profiler.
    MAX_TRACKED_LABEL_VALUES` distinct values. See `counts_truncated`."""
    distinct_count: int
    """`len(value_counts)` — a lower bound once `counts_truncated` is
    `True`."""
    counts_truncated: bool
    """`True` once distinct-value tracking hit `profiler.
    MAX_TRACKED_LABEL_VALUES`. Values already being tracked keep exact
    counts; no further distinct values are added."""


class FeatureAvailability(str, Enum):
    """Whether a canonical feature (from `app.services.feature_extraction.
    CANONICAL_COLUMN_NAMES`) has a source column in this dataset, purely
    from header/mapping information — independent of any row's actual
    value. There is deliberately no third "unusable" state here: that
    distinction (a column present but whose *values* can't be confidently
    interpreted) is `feature_extraction.FeatureStatus`'s job, a per-value
    concept this column-level availability check does not attempt to
    reproduce — see backend/README.md."""

    AVAILABLE = "available"
    MISSING = "missing"


@dataclass(frozen=True)
class ProfileWarning:
    """A structured, factual observation — never a subjective judgment.
    `code` is a stable machine-readable identifier; `message` is the
    human-readable, user-safe explanation (never a filesystem path or
    exception detail)."""

    code: str
    message: str


@dataclass(frozen=True)
class DatasetProfile:
    """The complete result of profiling one stored batch's CSV. Everything
    on this type is either an exact, actually-computed value, or
    explicitly marked as a bounded/unavailable approximation — nothing is
    fabricated. See `profiler.profile_dataset`."""

    identity: DatasetIdentity
    rows: RowSummary
    columns: tuple[ColumnProfile, ...]
    total_columns: int
    """Header column count, including duplicates (see
    `duplicate_column_names`) — the number of entries the CSV's header row
    actually had."""
    recognized_column_count: int
    unknown_column_count: int
    duplicate_column_names: tuple[str, ...]
    """Header column names (verbatim) that appear more than once. Ingestion
    (`app.services.ingestion`) keeps only the last value for a duplicated
    name in `NetworkFlowRecord.raw_features` — this field exists so that
    silent data loss is at least visible in the profile, not to imply this
    layer recovers it."""
    label_summary: LabelSummary
    attack_category_summary: LabelSummary
    canonical_feature_availability: dict[str, FeatureAvailability]
    warnings: tuple[ProfileWarning, ...]
