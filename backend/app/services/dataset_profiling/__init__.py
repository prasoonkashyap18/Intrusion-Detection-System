"""Dataset profiling: produces a truthful structural/statistical profile of
a stored batch's CSV, reusing ingestion (Step 15) for safe CSV reading and
the dataset-adapter registry (Step 18) for schema recognition.

See `profiler.py` for `profile_dataset`, and `profile.py` for the
`DatasetProfile` representation it returns. backend/README.md's "Dataset
Profiling" section covers the architecture, statistics, and limitations.

Performs NO machine learning: no prediction, classification, risk
calculation, or `DetectionResult` row is produced here, and no semantic
label normalization happens here either — see `profile.py`'s and
`profiler.py`'s docstrings.
"""

from __future__ import annotations

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
from app.services.dataset_profiling.profiler import profile_dataset

__all__ = [
    "BoundedValueCounter",
    "ColumnProfile",
    "DatasetIdentity",
    "DatasetProfile",
    "FeatureAvailability",
    "InferredType",
    "LabelSummary",
    "ProfileWarning",
    "RowSummary",
    "SchemaDetectionStatus",
    "profile_dataset",
]
