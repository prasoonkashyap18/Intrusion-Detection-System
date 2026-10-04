"""MappedFeatureRecord: the persisted form of one network-flow record's
mapped, ML-ready features (app.services.feature_mapping.MappedFeatureSet).

Deliberately independent of any ML algorithm: this table stores a
deterministic feature representation, never a prediction, confidence,
severity or risk score. No model has run by the time a row here is
written — see app/services/feature_persistence.py and
app/services/batch_processor.py for where these rows are created.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base


class MappedFeatureRecord(Base):
    """One CSV data row's mapped canonical features, persisted so a later
    ML/detection step can read them back without re-reading and
    re-parsing the batch's original CSV.

    `features` stores, per canonical feature name (see
    `app.services.feature_extraction.FEATURE_SCHEMA`), its parsed value,
    `FeatureStatus`, and provenance (source column + raw text) — exactly
    the content of one `app.services.feature_mapping.MappedFeatureSet`,
    structured as JSON rather than one column per feature so the table
    does not need to change shape if `FEATURE_SCHEMA` grows; see
    `feature_schema_version` for how a reader knows which schema/order
    produced a given row. This is NOT the original CSV row: unmapped
    dataset columns are recorded in `unknown_fields` only, not duplicated
    in full — see "Dataset Feature Persistence" in backend/README.md.
    """

    __tablename__ = "mapped_feature_records"
    __table_args__ = (
        # One persisted feature row per (batch, CSV data row). The existing
        # processing lifecycle (batch_processor.claim_for_processing) already
        # prevents a batch from being processed twice concurrently; this is
        # the database-level backstop against ever creating a second row
        # for the same batch/row pair, not a second concurrency mechanism.
        UniqueConstraint("batch_id", "row_number", name="uq_mapped_feature_records_batch_row"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("detection_batches.id"),
        nullable=False,
        index=True,
    )

    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    """1-based index among the batch's CSV data rows (the header is not
    counted) — matches `app.services.ingestion.NetworkFlowRecord.row_number`."""

    feature_schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    """`app.services.feature_extraction.FEATURE_SCHEMA_VERSION` at the time
    this row was written — lets a future reader detect a schema change
    rather than silently misinterpreting `features`' keys/order."""

    dataset_schema: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    """Which dataset adapter produced this row's source
    `CanonicalDatasetRecord` (its `schema_id`, e.g. `"nsl-kdd-style"`) —
    identifies the adapter that matched, not a verified claim about the
    data's real-world origin. See `app.services.dataset_adapters`."""

    features: Mapped[dict] = mapped_column(JSON, nullable=False)
    """`{canonical_name: {"value": float | int | null, "status":
    "present"/"missing"/"malformed"/"unsupported", "source_column":
    str | null, "raw_value": str | null}}` for every name in
    `FEATURE_SCHEMA`. A missing/malformed/unsupported feature's `"value"`
    is always `null` here — never `0` or any other fabricated stand-in;
    `"status"` distinguishes which of the three it is. Keys are written in
    `FEATURE_SCHEMA` order, but a reader must never rely on JSON key order
    for correctness — see `app.services.feature_persistence.
    load_feature_vector`, which rebuilds the vector by walking
    `FEATURE_SCHEMA` explicitly every time."""

    unknown_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    """Dataset columns with no canonical equivalent, verbatim — passed
    through from `MappedFeatureSet.unknown_fields`. Not the full original
    CSV row: every canonical column's text already lives in `features`,
    via each feature's own `"raw_value"`."""

    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """The dataset's own label text, exactly as `CanonicalDatasetRecord.
    label` reported it (e.g. `"normal"`, `"neptune"`, `"BENIGN"`) — `None`
    when the dataset provides no label column. Stored here, *not* inside
    `features`, so Step 22's training layer can supervise a model without
    this ever becoming part of the ML feature vector — see
    `app.services.feature_mapping`'s and `app.services.dataset_adapters.
    label_mapping`'s docstrings for why label text is never a feature, and
    how it is later turned into a benign/attack training target."""

    attack_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """The dataset's own attack-category text (e.g. UNSW-NB15's
    `attack_cat`), when the dataset provides a column distinct from its
    label — `None` otherwise. Metadata only, same as `label`."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    batch: Mapped["DetectionBatch"] = relationship("DetectionBatch", back_populates="mapped_feature_records")
