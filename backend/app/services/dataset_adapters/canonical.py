"""CanonicalDatasetRecord: the dataset-adapter layer's output.

One already-ingested CSV row, translated from a dataset's own column names
into this project's shared vocabulary. Independent of the SQLAlchemy ORM —
a plain dataclass, like `NetworkFlowRecord` and `NormalizedFlowFeatures`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.ingestion import NetworkFlowRecord


@dataclass(frozen=True)
class CanonicalDatasetRecord:
    """One row, as a `DatasetAdapter` understood it.

    Never produced by guessing: every field an adapter could not
    confidently map stays out of `canonical_fields` and is instead
    preserved in `unknown_fields`, and `label`/`attack_category` are only
    ever the dataset's own text from a column the adapter actually
    recognized — never inferred, never normalized, never defaulted.
    """

    row_number: int

    dataset_schema: str
    """Which adapter produced this record (its `schema_id`, e.g.
    "nsl-kdd-style") — identifies the adapter that matched, not a verified
    claim about the data's real-world origin."""

    source_record: NetworkFlowRecord
    """The original ingested row this was adapted from, kept in full.
    Every original column's raw text is already reachable via
    `source_record.raw_features`, so nothing an adapter does here can lose
    information ingestion already preserved — this field exists so a
    canonical record is traceable back to its source without the caller
    having to keep the original record around separately."""

    canonical_fields: dict[str, str]
    """Original (unparsed) text, re-keyed from the dataset's own column
    names to this project's canonical vocabulary — the same names
    `app.services.feature_extraction.CANONICAL_COLUMN_NAMES` recognizes —
    wherever the adapter could confidently identify the mapping. Values
    are copied verbatim; no type parsing happens here, since that is
    `feature_extraction.py`'s job and this layer must not duplicate it."""

    column_mapping: dict[str, str]
    """canonical field name -> the original source column name it came
    from (e.g. `{"source_ip": "srcip"}`), so a canonical field can always
    be traced back to exactly which column produced it."""

    unknown_fields: dict[str, str]
    """Every column this adapter's mapping did not recognize, verbatim,
    keyed by its original header text. This layer translates column names;
    it does not select features — a dataset's columns with no canonical
    equivalent are preserved here, not discarded."""

    label: str | None = None
    """The dataset's own label text, preserved exactly as written (e.g.
    `"normal"`, `"neptune"`, `"0"`, `"BENIGN"`) when the adapter recognizes
    a label column. `None` when no such column exists for this dataset
    family — never inferred from `canonical_fields` or defaulted."""

    attack_category: str | None = None
    """A separate attack-category/class column's text, when the dataset
    provides one distinct from its label column (e.g. UNSW-NB15's
    `attack_cat`). `None` when no such column exists — never derived from
    `label` or any other field."""

    label_column: str | None = None
    """The original source column name `label` was read from, when a label
    column was recognized — `None` when no label column exists. Companion
    to `column_mapping` for the same traceability reason, for the one
    field `column_mapping` cannot cover (a label column is handled before
    the canonical-field mapping, so it never appears in `column_mapping`
    itself)."""

    attack_category_column: str | None = None
    """The original source column name `attack_category` was read from,
    when an attack-category column was recognized — `None` otherwise."""
