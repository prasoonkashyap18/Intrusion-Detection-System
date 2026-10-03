"""Dataset feature mapping: CanonicalDatasetRecord -> ML-ready feature
representation.

    CanonicalDatasetRecord (app.services.dataset_adapters)
        |
    map_canonical_record()
        |
    MappedFeatureSet        <- deterministic, provenance-carrying,
                                algorithm-agnostic; the ML input contract
                                for whichever dataset family produced the
                                original row

This module performs NO machine learning: no prediction, classification,
risk/confidence/severity calculation, or `DetectionResult` row is produced
here. It does not fit or calculate any dataset-wide statistic (mean, std,
min/max, or any other normalization parameter) — see "No dataset-wide
fitting" below.

Single source of truth: this module does not reimplement type parsing or
define its own feature schema. It reads `CanonicalDatasetRecord.
canonical_fields` (already keyed with exactly the vocabulary
`feature_extraction.CANONICAL_COLUMN_NAMES` defines) and parses each value
via `feature_extraction.parse_canonical_field()` — the same per-type
parsing, the same `FEATURE_SCHEMA` order, the same `FeatureStatus`
semantics Steps 16/17 already established, dispatched directly by
canonical name rather than through `extract_raw_features()`'s raw-dataset-
column-name alias matching (which does not apply here: `canonical_fields`
is already-translated, not raw CSV text keyed by a dataset's own column
names). Nothing here invents an alternative numeric encoding.

No dataset-specific branching: this module has no knowledge of NSL-KDD,
UNSW-NB15, CICIDS, or any other dataset family. All of that lives in
`app.services.dataset_adapters` (Step 18), one layer upstream — this
module only knows the `CanonicalDatasetRecord` contract, never a dataset's
own column names.

Provenance: `MappedFeature` carries, for every canonical feature, not just
its parsed value and `FeatureStatus`, but also the *original* source
column name and raw text it came from (when the dataset provided one) —
richer than `feature_extraction.NormalizedFlowFeatures`, which only knows
whether ingestion recognized a column, not which dataset-specific column
name produced it. `_PREENCODING_SOURCE_NAME` is the only new mapping this
module adds, translating `FEATURE_SCHEMA`'s three *encoded* names
(`source_ip_numeric`, `destination_ip_numeric`, `protocol_number`) back to
the *pre-encoding* canonical names (`source_ip`, `destination_ip`,
`protocol`) `CanonicalDatasetRecord.column_mapping`/`canonical_fields` use
— a fixed, dataset-independent lookup, not a per-dataset branch.

No data leakage: `label`, `attack_category`, `dataset_schema`, `row_number`
never enter `MappedFeatureSet.features` or `feature_vector()`. They are
not features of a network flow; `dataset_schema`/`row_number` remain
available as separate metadata fields for traceability, exactly as
`NormalizedFlowFeatures` already keeps `row_number` outside its feature
vector. There is no batch ID or filename anywhere in this pipeline for a
mapped feature set to leak, since `NetworkFlowRecord`/`CanonicalDatasetRecord`
never carry either.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.feature_extraction import FEATURE_SCHEMA, FeatureStatus, parse_canonical_field

# FEATURE_SCHEMA's three encoded names -> the pre-encoding canonical name
# CanonicalDatasetRecord.column_mapping/canonical_fields use for the same
# concept. Every other FEATURE_SCHEMA entry (source_port, destination_port,
# flow_duration, packet_count, byte_count, packet_rate, byte_rate, the
# forward_/backward_ counts, tcp_flags) is spelled identically pre- and
# post-encoding, so it maps to itself. Fixed and dataset-independent: this
# is not a per-dataset branch, it is the one-time bridge between Step 18's
# and Step 16's two vocabularies.
_PREENCODING_SOURCE_NAME: dict[str, str] = {
    "source_ip_numeric": "source_ip",
    "destination_ip_numeric": "destination_ip",
    "protocol_number": "protocol",
}


@dataclass(frozen=True)
class MappedFeature:
    """One canonical feature's mapped value, with full provenance back to
    the source dataset. Never fabricated: `value` is `None` whenever
    `status` is not `PRESENT` — a missing, malformed, or unsupported
    source value is never coerced into a plausible-looking number."""

    canonical_name: str
    value: float | int | None
    status: FeatureStatus
    """Reused from `app.services.feature_extraction` — the same four
    outcomes (`PRESENT`/`MISSING`/`MALFORMED`/`UNSUPPORTED`), not a
    second, competing status vocabulary."""
    source_column: str | None
    """The original dataset column name this feature was read from, or
    `None` if this dataset provides no column for this canonical feature
    at all (not even an unparseable one)."""
    raw_value: str | None
    """The original, unparsed text from `source_column`, preserved
    verbatim — or `None` when `source_column` is `None`."""


@dataclass(frozen=True)
class MappedFeatureSet:
    """The ML-ready representation for one row: deterministic, algorithm-
    agnostic, and carrying no label/category/identity information that
    could leak into a feature vector.

    Not persisted anywhere (see "Persistence" in backend/README.md) —
    `map_canonical_record()` produces this value in memory for an
    immediate caller; nothing in this module writes to the database.
    """

    row_number: int
    dataset_schema: str
    """Which adapter produced the source `CanonicalDatasetRecord` — kept
    as metadata for logging/traceability, never as a feature (see
    `feature_vector()`)."""

    features: dict[str, MappedFeature]
    """Exactly `FEATURE_SCHEMA`'s names as keys. A `dict` for convenient
    by-name lookup, but never relied on for ordering — see
    `feature_vector()`."""

    unknown_fields: dict[str, str]
    """Every dataset column neither a canonical feature nor the label/
    attack-category, verbatim — passed straight through from
    `CanonicalDatasetRecord.unknown_fields`. Extra dataset-specific
    columns never corrupt `features`; they stay reachable here instead."""

    def feature_vector(self) -> list[float | int | None]:
        """The feature values in `FEATURE_SCHEMA`'s one true order —
        never CSV column order, dict insertion order, or adapter mapping
        order. This is what a future model step should consume."""
        return [self.features[name].value for name in FEATURE_SCHEMA]


def map_canonical_record(record: CanonicalDatasetRecord) -> MappedFeatureSet:
    """Maps one `CanonicalDatasetRecord` to a `MappedFeatureSet`.

    Deterministic: the same `CanonicalDatasetRecord` always yields the
    same `MappedFeatureSet` (same values, same statuses, same provenance),
    regardless of the order `canonical_fields`/`column_mapping` happen to
    iterate in — the result is always built by walking `FEATURE_SCHEMA`.

    `label`/`attack_category` are never in `canonical_fields` (the adapter
    layer routes them separately — see `CanonicalDatasetRecord`'s own
    docstring), so they cannot reach `features` or `feature_vector()`.
    """
    features: dict[str, MappedFeature] = {}
    for name in FEATURE_SCHEMA:
        preencoding_name = _PREENCODING_SOURCE_NAME.get(name, name)
        source_column = record.column_mapping.get(preencoding_name)
        raw_value = record.canonical_fields.get(preencoding_name)

        if raw_value is None:
            value: float | int | None = None
            status = FeatureStatus.MISSING
        else:
            value, status = parse_canonical_field(preencoding_name, raw_value)

        features[name] = MappedFeature(
            canonical_name=name,
            value=value,
            status=status,
            source_column=source_column,
            raw_value=raw_value,
        )

    return MappedFeatureSet(
        row_number=record.row_number,
        dataset_schema=record.dataset_schema,
        features=features,
        unknown_fields=dict(record.unknown_fields),
    )
