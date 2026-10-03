"""Shared mechanics for `DatasetAdapter` implementations.

Every adapter's `adapt()` does the same two things — rename a
`NetworkFlowRecord`'s columns through its own mapping, and pull out a
label/attack-category column when one is recognized — so that logic lives
here exactly once. Adapters supply only their own data (a required-column
set for detection, a column-name map, and which column(s) hold the label
and attack category); nothing here is dataset-specific.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.feature_extraction import normalize_column_name
from app.services.ingestion import NetworkFlowRecord


def header_matches(header: Sequence[str], required_normalized_names: Collection[str]) -> bool:
    """True if every name in `required_normalized_names` (already
    normalized — see `normalize_column_name`) appears, normalized,
    somewhere in `header`. The detection signature an adapter's
    `can_handle` is built from."""
    normalized_header = {normalize_column_name(name) for name in header}
    return set(required_normalized_names) <= normalized_header


def adapt_with_column_map(
    record: NetworkFlowRecord,
    *,
    schema_id: str,
    column_map: dict[str, str],
    label_columns: Collection[str] = (),
    attack_category_columns: Collection[str] = (),
) -> CanonicalDatasetRecord:
    """Builds a `CanonicalDatasetRecord` by renaming `record.raw_features`'
    columns through `column_map` (normalized source column name ->
    canonical field name, e.g. `{"srcip": "source_ip"}`).

    `label_columns`/`attack_category_columns` are normalized source column
    names to read label/category text from, when present; a record whose
    header lacks them simply leaves `label`/`attack_category` as `None` —
    never defaulted, never inferred from another column. An empty cell in
    a recognized label/category column is also treated as `None` (no text
    to preserve), consistent with how the rest of the pipeline treats an
    empty value as missing rather than as the empty string.
    """
    canonical_fields: dict[str, str] = {}
    column_mapping: dict[str, str] = {}
    unknown_fields: dict[str, str] = {}
    label: str | None = None
    attack_category: str | None = None

    for column, value in record.raw_features.items():
        normalized = normalize_column_name(column)

        if normalized in label_columns:
            label = value.strip() or None
            continue
        if normalized in attack_category_columns:
            attack_category = value.strip() or None
            continue

        canonical_name = column_map.get(normalized)
        if canonical_name is None:
            unknown_fields[column] = value
            continue
        canonical_fields[canonical_name] = value
        column_mapping[canonical_name] = column

    return CanonicalDatasetRecord(
        row_number=record.row_number,
        dataset_schema=schema_id,
        source_record=record,
        canonical_fields=canonical_fields,
        column_mapping=column_mapping,
        unknown_fields=unknown_fields,
        label=label,
        attack_category=attack_category,
    )
