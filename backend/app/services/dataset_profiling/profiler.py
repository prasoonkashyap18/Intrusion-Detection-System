"""Dataset profiling: produces a truthful, structural/statistical
`DatasetProfile` for a stored batch's CSV.

    Stored CSV
        |
    ingestion.read_header() / ingestion.ingest_batch()   <- Step 15, reused as-is
        |
    dataset_adapters.select_adapter()                     <- Step 18, reused as-is
        |
    DatasetProfiler (this module)
        |
    DatasetProfile

This module performs NO machine learning and creates no `DetectionResult`
row — it only counts and classifies what is actually present. It never
fabricates a row count, label, class, feature availability, percentage, or
dataset identity; anything it cannot determine safely is reported as
unknown/unavailable (`FeatureAvailability.MISSING`, `LabelSummary.
available = False`, `SchemaDetectionStatus.UNSUPPORTED`/`AMBIGUOUS`)
rather than guessed.

Dataset-adapter reuse: schema detection and column-name translation are not
reimplemented here. `select_adapter(header)` and (once, on a header-derived
record — see `_structural_mapping`) `adapter.adapt()` are the only places
this module learns which columns are canonical/label/attack-category; it
never inspects dataset-specific column names itself. This keeps the
dependency direction one-way: this module imports `dataset_adapters`;
`dataset_adapters` has no knowledge of this module.

Streaming and memory safety: the CSV's rows are read exactly once, via
`ingest_batch` (a generator). Every row updates a small, fixed set of
per-column counters and then is discarded — no list of rows, and no list of
any column's values, is ever held in memory. Two kinds of statistic are
intentionally size-bounded rather than exact: a column's distinct-value
count (`MAX_TRACKED_UNIQUE_VALUES`) and label/attack-category value counts
(`MAX_TRACKED_LABEL_VALUES`) — both via `BoundedValueCounter`, which keeps
already-tracked counts exact and flags itself once its cap is reached
rather than silently presenting a partial picture as complete. Every other
statistic (row/column counts, missing counts, type inference, min/max) is
exact and computed with O(1) additional memory per column — cheap enough
that no approximation is needed.

A structurally invalid CSV (missing file, unreadable, malformed, wrong row
width, no header) is not profiled partially: `profile_dataset` lets
ingestion's own `ApiException` propagate unchanged, the same failure
semantics already established for ingestion and processing — profiling a
broken file is a controlled failure, not a differently-shaped success.
"""

from __future__ import annotations

import math
from pathlib import Path

from app.services.dataset_adapters import AdapterSelection, select_adapter
from app.services.dataset_profiling.bounded_counter import BoundedValueCounter
from app.services.dataset_profiling.profile import (
    ColumnProfile,
    DatasetIdentity,
    DatasetProfile,
    FeatureAvailability,
    InferredType,
    LabelSummary,
    ProfileWarning,
    RowSummary,
    SchemaDetectionStatus,
)
from app.services.dataset_profiling.type_inference import ColumnTypeTracker
from app.services.feature_extraction import CANONICAL_COLUMN_NAMES
from app.services.ingestion import NetworkFlowRecord, ingest_batch, read_header

# Per-column distinct-value tracking cap. Generous enough to report exact
# uniqueness for realistic low/medium-cardinality network-flow columns
# (ports, protocols, flags, ...) while keeping memory bounded regardless of
# row count or column count.
MAX_TRACKED_UNIQUE_VALUES = 200

# Label/attack-category cardinality is normally tiny (a handful of classes).
# A higher cap than MAX_TRACKED_UNIQUE_VALUES on purpose: hitting this cap
# is itself a data-quality signal worth surfacing accurately before giving
# up on exact tracking.
MAX_TRACKED_LABEL_VALUES = 500

# A column at or above this missingness is flagged, not merely noted —
# chosen as "more missing than present," a clearly meaningful threshold
# rather than an arbitrary low bar that would flag routine sparsity.
HIGH_MISSINGNESS_THRESHOLD_PERCENT = 50.0


def profile_dataset(csv_path: Path) -> DatasetProfile:
    """Profiles a stored batch's CSV. Raises `ApiException` (propagated
    unchanged from `app.services.ingestion`) for the same structural
    failure modes ingestion itself refuses to process."""
    header = read_header(csv_path)
    unique_columns = list(dict.fromkeys(header))  # de-duplicated, order preserved
    duplicate_names = _find_duplicate_column_names(header)

    selection = select_adapter(header)
    identity = _build_identity(selection)

    canonical_column_mapping, label_column, attack_category_column = _structural_mapping(selection, unique_columns)
    recognized_originals = set(canonical_column_mapping.values())
    reverse_canonical_mapping = {original: canonical for canonical, original in canonical_column_mapping.items()}

    accumulators: dict[str, _ColumnAccumulator] = {name: _ColumnAccumulator() for name in unique_columns}
    label_counter = BoundedValueCounter(MAX_TRACKED_LABEL_VALUES)
    category_counter = BoundedValueCounter(MAX_TRACKED_LABEL_VALUES)

    total_rows = 0
    for record in ingest_batch(csv_path):
        total_rows += 1
        for column, value in record.raw_features.items():
            accumulators[column].observe(value)
        if label_column is not None:
            label_text = record.raw_features.get(label_column, "").strip()
            if label_text:
                label_counter.add(label_text)
        if attack_category_column is not None:
            category_text = record.raw_features.get(attack_category_column, "").strip()
            if category_text:
                category_counter.add(category_text)

    columns = tuple(
        _build_column_profile(
            name,
            accumulators[name],
            canonical_name=reverse_canonical_mapping.get(name),
            is_recognized=name in recognized_originals,
            is_label=(name == label_column),
            is_attack_category=(name == attack_category_column),
            total_rows=total_rows,
        )
        for name in unique_columns
    )

    label_summary = LabelSummary(
        available=label_column is not None,
        column_name=label_column,
        value_counts=label_counter.counts,
        distinct_count=label_counter.distinct_count,
        counts_truncated=label_counter.truncated,
    )
    attack_category_summary = LabelSummary(
        available=attack_category_column is not None,
        column_name=attack_category_column,
        value_counts=category_counter.counts,
        distinct_count=category_counter.distinct_count,
        counts_truncated=category_counter.truncated,
    )
    recognized_canonical_names = set(canonical_column_mapping)
    canonical_feature_availability = {
        name: (FeatureAvailability.AVAILABLE if name in recognized_canonical_names else FeatureAvailability.MISSING)
        for name in sorted(CANONICAL_COLUMN_NAMES)
    }

    warnings = _build_warnings(
        total_rows=total_rows,
        duplicate_names=duplicate_names,
        columns=columns,
        identity=identity,
        label_summary=label_summary,
    )

    return DatasetProfile(
        identity=identity,
        rows=RowSummary(total_rows=total_rows, rows_inspected=total_rows, is_exact=True),
        columns=columns,
        total_columns=len(header),
        recognized_column_count=len(recognized_originals),
        unknown_column_count=len(unique_columns) - len(recognized_originals) - sum(
            1 for name in (label_column, attack_category_column) if name is not None
        ),
        duplicate_column_names=duplicate_names,
        label_summary=label_summary,
        attack_category_summary=attack_category_summary,
        canonical_feature_availability=canonical_feature_availability,
        warnings=warnings,
    )


def _structural_mapping(
    selection: AdapterSelection, unique_columns: list[str]
) -> tuple[dict[str, str], str | None, str | None]:
    """Determines which original columns are canonical/label/attack-category
    purely from header information — independent of row count or content,
    so it gives the same answer for a 0-row file as for a 1,000,000-row
    one. Built by adapting one synthetic, all-empty-valued record: the
    adapter's column-to-field mapping depends only on column *names*, never
    on their values (see `app.services.dataset_adapters._common.
    adapt_with_column_map`), so this is exactly what a real row would
    produce, without needing one to exist.

    Returns (canonical field name -> original column name, label column
    name or None, attack-category column name or None).
    """
    if selection.adapter is None:
        return {}, None, None

    probe = NetworkFlowRecord(
        row_number=0,
        source_ip=None,
        destination_ip=None,
        source_port=None,
        destination_port=None,
        protocol=None,
        flow_timestamp=None,
        raw_features=dict.fromkeys(unique_columns, ""),
    )
    canonical = selection.adapter.adapt(probe)
    return dict(canonical.column_mapping), canonical.label_column, canonical.attack_category_column


def _build_identity(selection: AdapterSelection) -> DatasetIdentity:
    if selection.adapter is not None:
        return DatasetIdentity(
            status=SchemaDetectionStatus.RECOGNIZED,
            schema_id=selection.adapter.schema_id,
            schema_description=selection.adapter.description,
            candidate_schema_ids=selection.candidates,
        )
    status = SchemaDetectionStatus.AMBIGUOUS if selection.is_ambiguous else SchemaDetectionStatus.UNSUPPORTED
    return DatasetIdentity(status=status, schema_id=None, schema_description=None, candidate_schema_ids=selection.candidates)


def _find_duplicate_column_names(header: list[str]) -> tuple[str, ...]:
    seen: dict[str, int] = {}
    for name in header:
        seen[name] = seen.get(name, 0) + 1
    return tuple(name for name, count in seen.items() if count > 1)


class _ColumnAccumulator:
    """Mutable, streaming accumulator for one column's statistics. Never
    exposed outside this module — converted to an immutable `ColumnProfile`
    once the whole file has been read (see `_build_column_profile`)."""

    __slots__ = ("non_null_count", "missing_count", "type_tracker", "unique_tracker", "numeric_min", "numeric_max")

    def __init__(self) -> None:
        self.non_null_count = 0
        self.missing_count = 0
        self.type_tracker = ColumnTypeTracker()
        self.unique_tracker = BoundedValueCounter(MAX_TRACKED_UNIQUE_VALUES)
        self.numeric_min: float | None = None
        self.numeric_max: float | None = None

    def observe(self, raw_value: str) -> None:
        # Empty/whitespace-only is "missing" — the same convention already
        # established project-wide (app.services.ingestion and app.
        # services.feature_extraction both treat only this as absent, never
        # literal text like "0"/"N/A"/"unknown").
        if not raw_value.strip():
            self.missing_count += 1
            return
        self.non_null_count += 1
        self.type_tracker.observe(raw_value)
        self.unique_tracker.add(raw_value)
        numeric = _try_parse_finite_float(raw_value)
        if numeric is not None:
            self.numeric_min = numeric if self.numeric_min is None else min(self.numeric_min, numeric)
            self.numeric_max = numeric if self.numeric_max is None else max(self.numeric_max, numeric)


def _try_parse_finite_float(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _build_column_profile(
    name: str,
    acc: _ColumnAccumulator,
    *,
    canonical_name: str | None,
    is_recognized: bool,
    is_label: bool,
    is_attack_category: bool,
    total_rows: int,
) -> ColumnProfile:
    missing_percentage = (acc.missing_count / total_rows * 100) if total_rows > 0 else None
    return ColumnProfile(
        name=name,
        canonical_name=canonical_name,
        inferred_type=acc.type_tracker.inferred_type,
        non_null_count=acc.non_null_count,
        missing_count=acc.missing_count,
        missing_percentage=missing_percentage,
        unique_count=acc.unique_tracker.distinct_count,
        unique_count_is_exact=not acc.unique_tracker.truncated,
        is_recognized=is_recognized,
        is_label=is_label,
        is_attack_category=is_attack_category,
        minimum=acc.numeric_min,
        maximum=acc.numeric_max,
    )


def _build_warnings(
    *,
    total_rows: int,
    duplicate_names: tuple[str, ...],
    columns: tuple[ColumnProfile, ...],
    identity: DatasetIdentity,
    label_summary: LabelSummary,
) -> tuple[ProfileWarning, ...]:
    warnings: list[ProfileWarning] = []

    if total_rows == 0:
        warnings.append(ProfileWarning("empty_dataset", "The CSV has a header but no data rows."))

    if duplicate_names:
        warnings.append(
            ProfileWarning(
                "duplicate_columns",
                "Duplicate column name(s) in the header: "
                + ", ".join(duplicate_names)
                + ". Only the last column with each name is reflected in this profile "
                "(and in the batch's ingested data) — ingestion keeps one value per name.",
            )
        )

    for column in columns:
        if column.missing_percentage is not None and column.missing_percentage >= HIGH_MISSINGNESS_THRESHOLD_PERCENT:
            warnings.append(
                ProfileWarning(
                    "high_missingness",
                    f"Column '{column.name}' is {column.missing_percentage:.1f}% missing.",
                )
            )
        if column.inferred_type == InferredType.MIXED:
            warnings.append(
                ProfileWarning(
                    "mixed_type_column",
                    f"Column '{column.name}' contains more than one apparent data type.",
                )
            )

    if identity.status == SchemaDetectionStatus.UNSUPPORTED:
        warnings.append(
            ProfileWarning("unsupported_schema", "No registered dataset adapter recognized this CSV's header.")
        )
    if identity.status == SchemaDetectionStatus.AMBIGUOUS:
        warnings.append(
            ProfileWarning(
                "ambiguous_schema",
                "More than one dataset adapter's signature matched this header: "
                + ", ".join(identity.candidate_schema_ids)
                + ".",
            )
        )
    if not label_summary.available:
        warnings.append(ProfileWarning("no_recognizable_labels", "No label column was recognized for this dataset."))

    return tuple(warnings)
