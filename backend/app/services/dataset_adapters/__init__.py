"""Dataset-adapter layer: translates a dataset-specific CSV schema into the
project's canonical representation, without `ingestion.py` or
`feature_extraction.py` knowing about any individual dataset.

See `base.py` for the `DatasetAdapter` contract, `canonical.py` for
`CanonicalDatasetRecord`, `registry.py` for how a header is matched to an
adapter (or explicitly recognized as unsupported), and `label_mapping.py`
for the one place dataset-specific *label* text is turned into a binary
training target. backend/README.md's "Dataset Adapter Layer" section
covers the architecture and how to add a new adapter.

Wired into `app.services.batch_processor` as of Step 21 (persistence) —
see backend/README.md's "Dataset Feature Persistence" section.
"""

from __future__ import annotations

from app.services.dataset_adapters.base import DatasetAdapter
from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.dataset_adapters.label_mapping import BinaryLabel, to_binary_label
from app.services.dataset_adapters.registry import ADAPTERS, AdapterSelection, select_adapter

__all__ = [
    "ADAPTERS",
    "AdapterSelection",
    "BinaryLabel",
    "CanonicalDatasetRecord",
    "DatasetAdapter",
    "select_adapter",
    "to_binary_label",
]
